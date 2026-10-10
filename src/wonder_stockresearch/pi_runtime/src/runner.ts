// Project-local Pi runtime. All persistence, authorization and budgets belong to Python.
import { createInterface } from 'node:readline';
import { Agent, type AgentEvent, type AgentMessage, type AgentTool, type StreamFn } from '@earendil-works/pi-agent-core';
import {
  createModels,
  createAssistantMessageEventStream,
  type Api,
  type AssistantMessage,
  type AssistantMessageEventStream,
  type FetchFunction,
  type Message,
  type Model,
  type TSchema,
  type ToolResultMessage,
} from '@earendil-works/pi-ai';
import { deepseekProvider } from '@earendil-works/pi-ai/providers/deepseek';

type JsonObject = Record<string, unknown>;
interface RuntimeConfig {
  protocol_version: number;
  model: string;
  system: string;
  prompt: string;
  tools: { name: string; description: string; arguments: TSchema }[];
  history?: AgentMessage[];
  scripted?: boolean;
}
interface ToolRequest {
  toolCallId: string;
  name: string;
  arguments: JsonObject;
}
interface ToolReply {
  content: string;
  details?: JsonObject | null;
  error?: JsonObject | null;
}
type LifecycleType = Extract<AgentEvent, {
  type: 'agent_start' | 'agent_end' | 'turn_start' | 'turn_end' |
    'tool_execution_start' | 'tool_execution_end';
}>['type'];
interface LifecyclePayload {
  type: LifecycleType;
  toolCallId?: string;
  toolName?: string;
  isError?: boolean;
}
interface RpcContracts {
  ready: { request: { protocol_version: number }; response: { protocol_version: number } };
  context: { request: { messages: Message[] }; response: AgentMessage[] };
  model_start: { request: { messages: Message[] }; response: { max_tokens: number; repair_only?: boolean } };
  scripted_model: { request: { messages: Message[] }; response: { decision: JsonObject; consumed: number } };
  model_end: {
    request: { message: AssistantMessage; usageKnown: boolean; consumed: number | null };
    response: { repair?: string | null };
  };
  tool_check: { request: ToolRequest; response: JsonObject };
  tool: { request: ToolRequest; response: ToolReply };
  message: { request: { message: ToolResultMessage }; response: JsonObject };
  lifecycle: { request: LifecyclePayload; response: JsonObject };
  settled: { request: JsonObject; response: JsonObject };
}
type RpcEvent = keyof RpcContracts;
interface PendingRequest {
  resolve(value: unknown): void;
  reject(error: Error): void;
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function object(value: unknown): JsonObject {
  if (!isObject(value)) throw new Error('Expected protocol object');
  return value;
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const PROTOCOL_VERSION = 1;
const pending = new Map<number, PendingRequest>();
let nextId = 0;
let started = false;
const send = (value: JsonObject): void => {
  process.stdout.write(`${JSON.stringify({ ...value, protocol_version: PROTOCOL_VERSION })}\n`);
};

function rpc<TEvent extends RpcEvent>(
  event: TEvent,
  payload: RpcContracts[TEvent]['request'],
): Promise<RpcContracts[TEvent]['response']> {
  const response = new Promise<unknown>((resolve, reject: (error: Error) => void) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    send({ id, event, payload });
  });
  // Python's pi_protocol validates every event-specific response before sending it.
  // Keep that single protocol authority rather than duplicate Pydantic validators here.
  return response.then(value => value as RpcContracts[TEvent]['response']);
}

// Never send reasoning blocks, provider signatures, diagnostics, headers or raw errors to Python.
function visibleMessage(message: AssistantMessage): AssistantMessage;
function visibleMessage(message: ToolResultMessage): ToolResultMessage;
function visibleMessage(message: AgentMessage): Message;
function visibleMessage(message: AgentMessage): Message {
  const timestamp = message.timestamp ?? Date.now();
  switch (message.role) {
    case 'user':
      return { role: message.role, timestamp,
        content: typeof message.content === 'string' ? message.content :
          message.content.filter(item => item.type === 'text').map(item => ({ type: 'text', text: item.text })) };
    case 'assistant':
      return { role: message.role, timestamp,
        content: message.content.flatMap<AssistantMessage['content'][number]>(item => {
          if (item.type === 'text') return [{ type: 'text' as const, text: item.text }];
          if (item.type === 'toolCall') return [{ type: 'toolCall' as const,
            id: item.id, name: item.name, arguments: item.arguments }];
          return [];
        }),
        api: message.api, provider: message.provider, model: message.model,
        stopReason: message.stopReason, usage: message.usage };
    case 'toolResult':
      return { role: message.role, timestamp, toolCallId: message.toolCallId, toolName: message.toolName,
        content: message.content.filter(item => item.type === 'text').map(item => ({ type: 'text', text: item.text })),
        isError: Boolean(message.isError) };
    default:
      throw new Error('Unsupported agent message');
  }
}

function scriptedStream(
  model: Model<Api>, response: RpcContracts['scripted_model']['response'],
): AssistantMessageEventStream {
  const decision = response.decision;
  // Scripted decisions follow the same Python validation path as provider output.
  // Only this branch consumes tool/arguments; malformed decisions remain business errors.
  const call = decision as JsonObject & { tool: string; arguments?: JsonObject };
  const message: AssistantMessage = { role: 'assistant', api: model.api, provider: model.provider, model: model.id,
    content: decision['type'] === 'tool_call' ? [{ type: 'toolCall', id: `scripted-${nextId}`,
      name: call.tool, arguments: call.arguments ?? {} }] :
      [{ type: 'text', text: JSON.stringify(decision) }],
    usage: { input: response.consumed, output: 0, cacheRead: 0, cacheWrite: 0,
      totalTokens: response.consumed, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: decision['type'] === 'tool_call' ? 'toolUse' : 'stop', timestamp: Date.now() };
  const stream = createAssistantMessageEventStream();
  stream.push({ type: 'start', partial: message });
  stream.push({ type: 'done', reason: decision['type'] === 'tool_call' ? 'toolUse' : 'stop', message });
  return stream;
}

function runtimeConfig(value: unknown): RuntimeConfig {
  const config = object(value);
  if (config['protocol_version'] !== PROTOCOL_VERSION || typeof config['model'] !== 'string' ||
      typeof config['system'] !== 'string' || typeof config['prompt'] !== 'string' || !Array.isArray(config['tools'])) {
    throw new Error('Incompatible runtime configuration');
  }
  // The Python bridge validates tool schemas and visible history before starting this process.
  return config as unknown as RuntimeConfig;
}

async function run(config: RuntimeConfig): Promise<void> {
  const ready = await rpc('ready', { protocol_version: PROTOCOL_VERSION });
  if (ready.protocol_version !== PROTOCOL_VERSION) throw new Error('Incompatible runtime protocol');
  const models = createModels();
  models.setProvider(deepseekProvider());
  const template = models.getModel('deepseek', config.model) ?? models.getModels('deepseek')[0];
  if (!template) throw new Error('DeepSeek provider has no model template');
  const model: Model<Api> = { ...template, id: config.model,
    ...(process.env['WONDER_DEEPSEEK_BASE_URL'] ? { baseUrl: process.env['WONDER_DEEPSEEK_BASE_URL'] } : {}) };
  let observedUsage: number | null = null;
  let usageKnown = false;
  let rawBuffer = '';
  // Detect whether usage was really reported. Pi's default zero is not proof of a free request.
  const meteredFetch: FetchFunction = async (url, options) => {
    const response = await fetch(url, options);
    if (!response.body) return response;
    const decoder = new TextDecoder();
    const body = response.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        rawBuffer += decoder.decode(chunk, { stream: true });
        const lines = rawBuffer.split('\n'); rawBuffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          try {
            const data: unknown = JSON.parse(line.slice(5).trim());
            const usage = isObject(data) && isObject(data['usage']) ? data['usage'] : undefined;
            const total = usage?.['total_tokens'];
            if (typeof total === 'number' && Number.isSafeInteger(total) && total >= 0) {
              observedUsage = total;
            }
          } catch { /* SSE keepalive or [DONE]; never log wire data. */ }
        }
        if (rawBuffer.length > 1048576) rawBuffer = '';
        controller.enqueue(chunk);
      },
    }));
    return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
  };
  const tools: AgentTool<TSchema, { wonderReply: ToolReply }>[] = config.tools.map(definition => ({
    name: definition.name, label: definition.name, description: definition.description,
    parameters: definition.arguments, executionMode: 'sequential',
    execute: async (toolCallId, args: unknown) => {
      const result = await rpc('tool', { toolCallId, name: definition.name, arguments: object(args) });
      // Python has committed the actual result/receipts; only a bounded view crosses the bridge.
      return { content: [], details: { wonderReply: result } };
    },
  }));
  const streamFn: StreamFn = async (selectedModel, context, options) => {
    observedUsage = null; usageKnown = false; rawBuffer = '';
    const limits = await rpc('model_start', { messages: context.messages.map(visibleMessage) });
    if (config.scripted) {
      const response = await rpc('scripted_model', { messages: context.messages.map(visibleMessage) });
      usageKnown = true;
      return scriptedStream(selectedModel, response);
    }
    const requestContext = limits.repair_only ? { ...context, tools: [] } : context;
    return models.streamSimple(selectedModel, requestContext, { ...options, maxTokens: limits.max_tokens,
      temperature: 0.2, maxRetries: 0, timeoutMs: 300000, fetch: meteredFetch,
      onPayload: payload => ({ ...object(payload), thinking: { type: 'disabled' },
        parallel_tool_calls: false }),
    });
  };
  const agent = new Agent({
    initialState: { model, systemPrompt: config.system, tools, messages: config.history ?? [], thinkingLevel: 'off' },
    toolExecution: 'sequential',
    beforeToolCall: async ({ toolCall, args }) => {
      await rpc('tool_check', { toolCallId: toolCall.id, name: toolCall.name, arguments: object(args) });
      return undefined;
    },
    afterToolCall: async ({ result }) => {
      const details: unknown = result.details;
      const value = isObject(details) ? details['wonderReply'] : undefined;
      if (!value) return undefined;
      // Only our execute() stores wonderReply, after a validated Python RPC response.
      const reply = value as ToolReply;
      return { content: [{ type: 'text', text: reply.content }], details: reply.details ?? {},
        isError: Boolean(reply.error) };
    },
    transformContext: async messages => rpc('context', { messages: messages.map(visibleMessage) }),
    streamFn,
  });
  agent.subscribe(async (event: AgentEvent) => {
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      const message = visibleMessage(event.message);
      const response = await rpc('model_end', { message,
        usageKnown: usageKnown || observedUsage !== null,
        consumed: usageKnown ? message.usage.totalTokens : observedUsage });
      if (response.repair) agent.followUp({ role: 'user', content: response.repair, timestamp: Date.now() });
    } else if (event.type === 'message_end' && event.message.role === 'toolResult') {
      await rpc('message', { message: visibleMessage(event.message) });
    } else {
      switch (event.type) {
        case 'agent_start': case 'agent_end': case 'turn_start': case 'turn_end':
          await rpc('lifecycle', { type: event.type });
          break;
        case 'tool_execution_start': case 'tool_execution_end': {
          const payload: LifecyclePayload = { type: event.type, toolCallId: event.toolCallId, toolName: event.toolName };
          if (event.type === 'tool_execution_end') payload.isError = Boolean(event.isError);
          await rpc('lifecycle', payload);
          break;
        }
      }
    }
  });
  await agent.prompt({ role: 'user', content: config.prompt, timestamp: Date.now() });
  await rpc('settled', {});
  send({ event: 'done' });
  input.close();
}

input.on('line', line => {
  try {
    const value: unknown = JSON.parse(line);
    if (!started) {
      started = true;
      void run(runtimeConfig(value)).catch(() => { send({ event: 'failed', code: 'pi_runtime_failed' }); input.close(); process.exitCode = 1; });
    } else {
      const message = object(value);
      const id = message['id'];
      if (message['protocol_version'] !== PROTOCOL_VERSION || typeof id !== 'number' || !Number.isSafeInteger(id) ||
          typeof message['ok'] !== 'boolean') throw new Error('Invalid runtime response');
      const request = pending.get(id);
      if (!request) throw new Error('Unknown response');
      pending.delete(id);
      if (message['ok']) request.resolve(message['result']);
      else request.reject(new Error('Python rejected runtime request'));
    }
  } catch {
    send({ event: 'failed', code: 'pi_runtime_failed' }); input.close(); process.exitCode = 1;
  }
});
