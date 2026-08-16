/**
 * TraceViewer — 会话 trace 可视化回放抽屉。
 *
 * 读取后端 /api/trace/list 与 /api/trace/{session_id}，把扁平 JSONL 事件
 * 按「轮次（user_message 分隔）」分组，并按 span_id 配对 llm / tool 的请求与响应，
 * 渲染为可展开的时间轴。支持「自动播放」逐条高亮。
 */

import React, { useEffect, useMemo, useState } from 'react';
import * as Diff from 'diff';
import {
  Drawer, Button, List, Tag, Typography, Collapse, Spin, Empty, Modal, Alert, message, Segmented,
} from 'antd';
import {
  ReloadOutlined, CaretRightOutlined, PauseOutlined, StepBackwardOutlined,
  RobotOutlined, ToolOutlined, CheckCircleOutlined, WarningOutlined, UserOutlined,
  CloseCircleOutlined, SyncOutlined,
} from '@ant-design/icons';
import { useUiStore } from '../../stores/uiStore';
import {
  listTraces, getTrace, replayTrace,
  type TraceMeta, type TraceDetail, type TraceReplayResult,
} from '../../services/api';
import './TraceViewer.css';

const { Text } = Typography;

type TraceEvent = Record<string, any>;

interface LlmItem { kind: 'llm'; request: TraceEvent; response?: TraceEvent; }
interface ToolItem { kind: 'tool'; call: TraceEvent; result?: TraceEvent; }
interface StepItem { kind: 'step'; event: TraceEvent; }
interface DoneItem { kind: 'done'; event: TraceEvent; }
interface ErrorItem { kind: 'error'; event: TraceEvent; }
type Item = LlmItem | ToolItem | StepItem | DoneItem | ErrorItem;

interface Turn { id: number; userMessage: string; projectFile: string; items: Item[]; }

// ── 工具函数 ──────────────────────────────────────────

function truncate(s: string, n = 4000): string {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + ` … (+${s.length - n} 字符)` : s;
}

function pretty(v: any): string {
  if (v === undefined || v === null) return '';
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fmtTime(ms: number | null | undefined): string {
  if (!ms) return '';
  return new Date(ms).toLocaleTimeString();
}

function baseName(path: string): string {
  if (!path) return '';
  return path.split(/[\\/]/).pop() || path;
}

// ── 回放结果缓存（localStorage，跨抽屉/页面刷新保留）──────────
const REPLAY_CACHE_PREFIX = 'traceReplay:';

function readReplayCache(key: string): TraceReplayResult | null {
  try {
    const raw = localStorage.getItem(REPLAY_CACHE_PREFIX + key);
    return raw ? (JSON.parse(raw) as TraceReplayResult) : null;
  } catch {
    return null;
  }
}

function writeReplayCache(key: string, result: TraceReplayResult): void {
  try {
    localStorage.setItem(REPLAY_CACHE_PREFIX + key, JSON.stringify(result));
  } catch {
    // localStorage 超限（结果过大）时静默放弃缓存，不影响本次展示
  }
}

// ── 事件流 → 轮次树 ───────────────────────────────────

function buildTurns(events: TraceEvent[]): Turn[] {
  const turns: Turn[] = [];
  let cur: Turn = { id: 0, userMessage: '', projectFile: '', items: [] };
  turns.push(cur);
  const llmBySpan = new Map<string, LlmItem>();
  const toolBySpan = new Map<string, ToolItem>();

  for (const ev of events) {
    switch (ev.event_type) {
      case 'user_message':
        cur = { id: turns.length, userMessage: ev.message || '', projectFile: ev.project_file || '', items: [] };
        turns.push(cur);
        break;
      case 'llm_request': {
        const item: LlmItem = { kind: 'llm', request: ev };
        llmBySpan.set(ev.span_id, item);
        cur.items.push(item);
        break;
      }
      case 'llm_response': {
        const item = llmBySpan.get(ev.span_id);
        if (item) item.response = ev;
        else cur.items.push({ kind: 'llm', request: ev, response: ev });
        break;
      }
      case 'tool_call': {
        const item: ToolItem = { kind: 'tool', call: ev };
        toolBySpan.set(ev.span_id, item);
        cur.items.push(item);
        break;
      }
      case 'tool_result': {
        const item = toolBySpan.get(ev.span_id);
        if (item) item.result = ev;
        else cur.items.push({ kind: 'tool', call: ev, result: ev });
        break;
      }
      case 'agent_step':
        cur.items.push({ kind: 'step', event: ev });
        break;
      case 'done':
        cur.items.push({ kind: 'done', event: ev });
        break;
      case 'error':
        cur.items.push({ kind: 'error', event: ev });
        break;
      default:
        // session_start / session_end / review_* / kg_inject：暂不在时间轴单独渲染
        break;
    }
  }
  return turns.filter((t) => t.items.length > 0 || t.userMessage);
}

// ── 渲染子组件 ────────────────────────────────────────

function renderMessages(systemPrompt?: string, messages?: any[]): React.ReactNode {
  const msgs: Array<{ role: string; content: string }> = [];
  if (systemPrompt) msgs.push({ role: 'system', content: systemPrompt });
  for (const m of messages || []) {
    if (!m || typeof m !== 'object') continue;
    let role = m.role || '?';
    if (role === 'tool') role = 'tool（模型收到的返回，可能已截断）';
    let content = m.content;
    if (Array.isArray(content)) {
      content = content.map((p: any) => (typeof p === 'string' ? p : p?.text || p?.type || '')).join('\n');
    }
    if (m.tool_calls) {
      content = (content ? content + '\n' : '') + '[tool_calls]\n' + pretty(m.tool_calls);
    }
    msgs.push({ role, content: typeof content === 'string' ? content : pretty(content) });
  }
  if (msgs.length === 0) return <Text type="secondary">(无消息)</Text>;
  const hasTool = (messages || []).some((m) => m && m.role === 'tool');
  return (
    <div className="trace-msgs">
      {hasTool && (
        <div style={{ fontSize: 11, color: '#999', marginBottom: 4 }}>
          提示：tool 消息为模型实际收到的截断版（超过 2000 字被截），完整返回见对应工具卡片。
        </div>
      )}
      {msgs.map((m, i) => (
        <div className="trace-msg" key={i}>
          <div className="trace-msg-role">{m.role}</div>
          <pre className="trace-pre">{truncate(m.content, 3000)}</pre>
        </div>
      ))}
    </div>
  );
}

function renderToolSchema(tools: any[]): React.ReactNode {
  return (
    <div>
      <div className="trace-msgs">
        {tools.map((t, i) => (
          <div className="trace-msg" key={i}>
            <div className="trace-msg-role">{t?.function?.name || '?'}</div>
            <div style={{ fontSize: 11, color: '#888' }}>{t?.function?.description || ''}</div>
          </div>
        ))}
      </div>
      <pre className="trace-pre">{truncate(pretty(tools), 4000)}</pre>
    </div>
  );
}

function renderLlm(item: LlmItem): React.ReactNode {
  const req = item.request;
  const res = item.response;
  const usage = res?.usage;
  const tokens = usage
    ? `${usage.prompt_tokens ?? '?'}/${usage.completion_tokens ?? '?'}`
    : '';
  const panels: Array<{ key: string; label: string; children: React.ReactNode }> = [
    { key: 'prompt', label: 'Prompt', children: renderMessages(req.system_prompt, req.messages) },
  ];
  if (req.tools?.length) {
    panels.unshift({
      key: 'tools',
      label: `工具 schema (${req.tools.length})`,
      children: renderToolSchema(req.tools),
    });
  }
  if (res) {
    panels.push({
      key: 'response',
      label: `Response${res.error ? ' (error)' : ''}`,
      children: res.error
        ? <pre className="trace-pre trace-error-text">{truncate(String(res.error))}</pre>
        : (
          <div>
            {res.content
              ? <pre className="trace-pre">{truncate(String(res.content))}</pre>
              : <Text type="secondary">(空内容)</Text>}
            {res.tool_calls?.length
              ? <pre className="trace-pre">{truncate(pretty(res.tool_calls))}</pre>
              : null}
          </div>
        ),
    });
  }
  return (
    <div className="trace-card trace-llm">
      <div className="trace-card-head">
        <RobotOutlined className="trace-icon llm" />
        <span className="trace-title">LLM</span>
        {req.model ? <Tag>{req.model}</Tag> : null}
        {req.span_path ? <Tag color="geekblue">{req.span_path}</Tag> : null}
        {req.temperature != null ? <Tag color="default">t={req.temperature}</Tag> : null}
        {req.max_tokens != null ? <Tag color="default">max={req.max_tokens}</Tag> : null}
        {req.tool_choice ? <Tag color="cyan">choice={req.tool_choice}</Tag> : null}
        {req.response_format ? <Tag color="purple">json</Tag> : null}
        {tokens ? <Tag color="blue">{tokens} tok</Tag> : null}
        {res?.duration_ms != null ? <span className="trace-meta">{res.duration_ms}ms</span> : null}
      </div>
      <Collapse ghost items={panels} />
    </div>
  );
}

function renderTool(item: ToolItem): React.ReactNode {
  const call = item.call;
  const res = item.result;
  const obsLabel = res?.fed_truncated
    ? `返回 · 完整(模型仅看前${res.fed_length}字)`
    : '返回';
  const panels: Array<{ key: string; label: string; children: React.ReactNode }> = [
    { key: 'args', label: '参数', children: <pre className="trace-pre">{truncate(pretty(call.arguments))}</pre> },
  ];
  if (res) {
    panels.push({
      key: 'obs',
      label: `${obsLabel}${res.error ? ' (error)' : ''}`,
      children: res.error
        ? <pre className="trace-pre trace-error-text">{truncate(String(res.error))}</pre>
        : <pre className="trace-pre">{truncate(String(res.observation ?? ''))}</pre>,
    });
  }
  return (
    <div className="trace-card trace-tool">
      <div className="trace-card-head">
        <ToolOutlined className="trace-icon tool" />
        <span className="trace-title">{call.tool_name || 'tool'}</span>
        {res?.fed_truncated ? (
          <Tag color="orange">模型仅收到前 {res.fed_length} 字</Tag>
        ) : null}
        {res?.duration_ms != null ? <span className="trace-meta">{res.duration_ms}ms</span> : null}
      </div>
      <Collapse ghost items={panels} />
    </div>
  );
}

function renderStep(ev: TraceEvent): React.ReactNode {
  const actions: string[] = ev.actions || [];
  return (
    <div className="trace-card trace-step">
      <div className="trace-card-head">
        <span className="trace-step-no">Step {ev.step}</span>
        {actions.length > 0 ? <Tag>{actions.join(', ')}</Tag> : null}
      </div>
      {ev.thought ? <div className="trace-thought">{truncate(String(ev.thought), 200)}</div> : null}
    </div>
  );
}

function renderDone(ev: TraceEvent): React.ReactNode {
  return (
    <div className="trace-card trace-done">
      <div className="trace-card-head">
        <CheckCircleOutlined className="trace-icon done" />
        <span className="trace-title">完成</span>
      </div>
      <div className="trace-thought">{truncate(String(ev.answer || ''), 2000)}</div>
    </div>
  );
}

function renderError(ev: TraceEvent): React.ReactNode {
  return (
    <div className="trace-card trace-error">
      <div className="trace-card-head">
        <WarningOutlined className="trace-icon err" />
        <span className="trace-title">错误{ev.source ? ` · ${ev.source}` : ''}</span>
      </div>
      <div className="trace-thought">{truncate(String(ev.message || ''), 1000)}</div>
    </div>
  );
}

function renderItem(item: Item): React.ReactNode {
  switch (item.kind) {
    case 'llm': return renderLlm(item);
    case 'tool': return renderTool(item);
    case 'step': return renderStep(item.event);
    case 'done': return renderDone(item.event);
    case 'error': return renderError(item.event);
  }
}

function renderWordDiff(recorded: string, final: string): React.ReactNode {
  const parts = Diff.diffWords(String(recorded || ''), String(final || ''));
  return (
    <div className="trace-diff">
      {parts.map((p, i) => (
        <span
          key={i}
          style={p.added
            ? { backgroundColor: '#e6ffec', color: '#1a7f37' }
            : p.removed
              ? { backgroundColor: '#ffebe9', color: '#cf222e', textDecoration: 'line-through' }
              : undefined}
        >
          {p.value}
        </span>
      ))}
    </div>
  );
}

// ── 主组件 ────────────────────────────────────────────

const TraceViewer: React.FC = () => {
  const { traceVisible, setTraceVisible } = useUiStore();

  const [traces, setTraces] = useState<TraceMeta[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const [playing, setPlaying] = useState(false);
  const [playIndex, setPlayIndex] = useState(-1);

  const [replaying, setReplaying] = useState(false);
  const [replayResult, setReplayResult] = useState<TraceReplayResult | null>(null);
  const [replayModalOpen, setReplayModalOpen] = useState(false);
  const [replayFromCache, setReplayFromCache] = useState(false);
  const [replayMode, setReplayMode] = useState<'mock' | 'rerun'>('mock');

  const refreshList = async () => {
    setLoadingList(true);
    try {
      const list = await listTraces();
      setTraces(list);
      if (list.length > 0 && !selected) {
        selectSession(list[0].session_id);
      }
    } catch {
      // 后端未启动等：静默保留旧列表
    } finally {
      setLoadingList(false);
    }
  };

  const selectSession = async (sessionId: string) => {
    setSelected(sessionId);
    setLoadingDetail(true);
    setPlaying(false);
    setPlayIndex(-1);
    try {
      setDetail(await getTrace(sessionId));
    } catch {
      setDetail(null);
    } finally {
      setLoadingDetail(false);
    }
  };

  useEffect(() => {
    if (traceVisible) refreshList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traceVisible]);

  const turns = useMemo(() => (detail ? buildTurns(detail.events) : []), [detail]);

  // 渲染行：轮次头 + 事件项（事件项带全局播放序号）
  type Row = { kind: 'turn'; turn: Turn } | { kind: 'item'; item: Item; playIndex: number };
  const rows: Row[] = useMemo(() => {
    const out: Row[] = [];
    let play = 0;
    for (const turn of turns) {
      out.push({ kind: 'turn', turn });
      for (const item of turn.items) {
        out.push({ kind: 'item', item, playIndex: play });
        play++;
      }
    }
    return out;
  }, [turns]);
  const totalItems = rows.filter((r) => r.kind === 'item').length;

  // 自动播放推进
  useEffect(() => {
    if (!playing) return;
    if (playIndex >= totalItems - 1) {
      setPlaying(false);
      return;
    }
    const t = setInterval(() => {
      setPlayIndex((i) => {
        if (i >= totalItems - 1) {
          setPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, 700);
    return () => clearInterval(t);
  }, [playing, playIndex, totalItems]);

  // 高亮项滚动到可视区
  useEffect(() => {
    if (playIndex >= 0) {
      document.getElementById(`trace-item-${playIndex}`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [playIndex]);

  const startPlay = () => {
    if (totalItems === 0) return;
    setPlayIndex(0);
    setPlaying(true);
  };

  const runReplay = async (force = false) => {
    if (!selected) return;
    const cacheKey = `${selected}:${replayMode}`;
    // 命中缓存且非强制重跑：直接展示上次结果，不重新执行
    if (!force) {
      const cached = readReplayCache(cacheKey);
      if (cached) {
        setReplayFromCache(true);
        setReplayResult(cached);
        setReplayModalOpen(true);
        return;
      }
    }
    setReplaying(true);
    try {
      const result = await replayTrace(selected, replayMode);
      writeReplayCache(cacheKey, result);
      setReplayFromCache(false);
      setReplayResult(result);
      setReplayModalOpen(true);
    } catch (e: any) {
      message.error('回放失败: ' + (e?.response?.data?.detail || String(e)));
    } finally {
      setReplaying(false);
    }
  };

  const handleClose = () => {
    setPlaying(false);
    setPlayIndex(-1);
    setTraceVisible(false);
  };

  return (
    <Drawer
      title="Trace 回放"
      width={1000}
      open={traceVisible}
      onClose={handleClose}
      styles={{ body: { padding: 0, display: 'flex', overflow: 'hidden' } }}
      extra={
        <div style={{ display: 'flex', gap: 8 }}>
          <Button icon={<ReloadOutlined />} onClick={refreshList} loading={loadingList}>
            刷新
          </Button>
          <Segmented
            size="small"
            value={replayMode}
            onChange={(v) => setReplayMode(v as 'mock' | 'rerun')}
            options={[
              { label: 'Mock', value: 'mock' },
              { label: 'Rerun(真LLM)', value: 'rerun' },
            ]}
          />
          <Button icon={<SyncOutlined />} onClick={() => runReplay()} loading={replaying} disabled={!selected}>
            回放执行
          </Button>
          {!playing ? (
            <Button type="primary" icon={<CaretRightOutlined />} onClick={startPlay} disabled={totalItems === 0}>
              自动播放
            </Button>
          ) : (
            <Button icon={<PauseOutlined />} onClick={() => setPlaying(false)}>
              暂停
            </Button>
          )}
          <Button icon={<StepBackwardOutlined />} onClick={() => { setPlaying(false); setPlayIndex(-1); }} disabled={playIndex < 0}>
            重置
          </Button>
        </div>
      }
    >
      <div className="trace-viewer-body">
        {/* 左侧：会话列表 */}
        <div className="trace-session-list">
          {loadingList && traces.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center' }}><Spin /></div>
          ) : traces.length === 0 ? (
            <Empty description="暂无 trace" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginTop: 24 }} />
          ) : (
            <List
              size="small"
              dataSource={traces}
              renderItem={(t) => (
                <List.Item
                  className={selected === t.session_id ? 'trace-session-item active' : 'trace-session-item'}
                  onClick={() => selectSession(t.session_id)}
                >
                  <List.Item.Meta
                    title={<span style={{ fontSize: 13 }}>{t.session_id}</span>}
                    description={
                      <span style={{ fontSize: 11, color: '#999' }}>
                        {t.events} 事件 · {fmtSize(t.size)}
                        {t.first_ts_ms ? ` · ${fmtTime(t.first_ts_ms)}` : ''}
                      </span>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </div>

        {/* 右侧：时间轴 */}
        <div className="trace-timeline">
          {loadingDetail ? (
            <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div>
          ) : !detail ? (
            <Empty description="选择一个会话查看" style={{ marginTop: 48 }} />
          ) : (
            rows.map((row, i) => {
              if (row.kind === 'turn') {
                return (
                  <div className="trace-turn-header" key={`turn-${row.turn.id}`}>
                    <UserOutlined style={{ marginRight: 6 }} />
                    {row.turn.userMessage
                      ? truncate(row.turn.userMessage, 120)
                      : '（会话开始 / 独立优化）'}
                    {row.turn.projectFile
                      ? <Tag style={{ marginLeft: 8 }}>{baseName(row.turn.projectFile)}</Tag>
                      : null}
                  </div>
                );
              }
              const active = row.playIndex === playIndex && playing;
              return (
                <div
                  id={`trace-item-${row.playIndex}`}
                  key={`item-${row.playIndex}`}
                  className={active ? 'trace-item trace-item-active' : 'trace-item'}
                >
                  {renderItem(row.item)}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* 回放结果弹窗 */}
      <Modal
        title="回放执行结果"
        open={replayModalOpen}
        onCancel={() => setReplayModalOpen(false)}
        footer={[
          <Button
            key="rerun"
            icon={<SyncOutlined />}
            loading={replaying}
            onClick={() => runReplay(true)}
          >
            重新执行
          </Button>,
          <Button key="close" type="primary" onClick={() => setReplayModalOpen(false)}>
            关闭
          </Button>,
        ]}
        width={720}
      >
        {replayResult && (
          <div>
            <Alert
              type={replayResult.all_matched ? 'success' : 'warning'}
              showIcon
              message={
                replayResult.all_matched
                  ? '回放完全一致：所有轮次最终答案与记录逐字匹配。'
                  : '回放存在不一致，请查看下方各轮明细。'
              }
              style={{ marginBottom: 12 }}
            />
            <div style={{ marginBottom: 12, fontSize: 13, color: '#666' }}>
              模式 {replayResult.mode === 'mock' ? 'Mock（全模拟）' : 'Rerun（真 LLM）'} ·
              LLM 记录 {replayResult.llm_total} 次 · 工具 {replayResult.tool_calls}/{replayResult.tool_total}
              {replayFromCache ? <Tag color="default" style={{ marginLeft: 8 }}>上次结果</Tag> : null}
            </div>
            <Collapse
              ghost
              items={replayResult.turns.map((t, i) => ({
                key: String(i),
                label: (
                  <span style={{ fontSize: 13 }}>
                    {t.matches
                      ? <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 6 }} />
                      : t.error
                        ? <WarningOutlined style={{ color: '#ff4d4f', marginRight: 6 }} />
                        : <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: 6 }} />}
                    第 {i + 1} 轮{t.error ? ' · 回放中断' : t.matches ? ' · 匹配' : ' · 不匹配'}
                    {' — '}{truncate(t.user_message, 40)}
                  </span>
                ),
                children: (
                  <div>
                    {t.error && (
                      <Alert type="error" showIcon message={t.error} style={{ marginBottom: 8 }} />
                    )}
                    {t.recorded_answer == null ? (
                      <Text type="secondary">（该轮无记录答案）</Text>
                    ) : t.matches ? (
                      <div>
                        <Tag color="green" style={{ marginBottom: 6 }}>内容一致</Tag>
                        <pre className="trace-pre">{t.final_answer}</pre>
                      </div>
                    ) : (
                      <div>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 6 }}>
                          逐词差异（<span style={{ color: '#cf222e' }}>红=记录</span> / <span style={{ color: '#1a7f37' }}>绿=回放</span>）
                        </div>
                        {renderWordDiff(t.recorded_answer || '', t.final_answer)}
                        <div style={{ marginTop: 12 }}>
                          <div style={{ fontSize: 12, fontWeight: 600, color: '#cf222e', marginBottom: 4 }}>
                            记录答案（当时）
                          </div>
                          <pre className="trace-pre">{t.recorded_answer}</pre>
                          <div style={{ fontSize: 12, fontWeight: 600, color: '#1a7f37', marginBottom: 4, marginTop: 8 }}>
                            回放结果（现在）
                          </div>
                          <pre className="trace-pre">{t.final_answer}</pre>
                        </div>
                      </div>
                    )}
                  </div>
                ),
              }))}
            />
          </div>
        )}
      </Modal>
    </Drawer>
  );
};

export default TraceViewer;
