'use client';

import { RequirementCard } from '@/app/components/requirement-card';
import { DashboardHeader } from '@/app/components/dashboard-header';
import { StatsBar } from '@/app/components/stats-bar';
import { useState, useEffect, useCallback } from 'react';
import {
  Terminal,
  Send,
  Database,
  RefreshCw,
  Info,
  MessageSquare,
  Inbox,
  Zap,
  Clock,
  ArrowRight,
  CheckCircle,
} from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL 
  ? `${process.env.NEXT_PUBLIC_API_URL}/api` 
  : 'http://localhost:8000/api';

type DashboardStats = {
  pendingReview: number;
  newDiscoveries: number;
  contradictions: number;
  archived: number;
  processedToday: number;
};

type ActionItem = {
  id: string;
  type: 'new_discovery' | 'contradiction_alert';
  slackMessage?: string;
  suggestedTitle?: string;
  suggestedDescription?: string;
  acceptanceCriteria?: string[];
  conflictTicket?: string;
  conflictTicketUrl?: string;
  conflictAnalysis?: string;
  similarityScore?: number | null;
  isHardConstraint?: boolean;
  confidenceScore?: number | null;
  sourceChannel?: string;
  author?: string;
  createdAt?: string;
};

type RecentMessage = {
  id: string;
  channel: string;
  author: string;
  status: string;
  createdAt: string;
};

type FeedbackEntry = {
  id: number;
  text: string;
  type: 'success' | 'error' | 'info' | 'processing';
};

// ── Preset messages for quick testing ───────────────────────────────────────
const PRESET_MESSAGES = [
  {
    label: '🔵 Hard Constraint',
    text: 'The login button MUST use color #1A73E8 on all mobile views per the new design system guidelines. This is mandatory for the Q3 release.',
    channel: '#product-design',
    user: 'priya_pm',
  },
  {
    label: '⚡ Timeline Change',
    text: '2FA implementation deadline has been moved up to end of August instead of September. This is now a P0 for the auth team.',
    channel: '#engineering',
    user: 'raj_arch',
  },
  {
    label: '⚠️ Contradiction',
    text: 'The login button must be green (#22c55e) on mobile. Our brand refresh requires this change across all platforms.',
    channel: '#design',
    user: 'design_lead',
  },
  {
    label: '💬 Noise (filtered)',
    text: 'Good morning team! Hope everyone had a great weekend 🎉',
    channel: '#general',
    user: 'john_dev',
  },
  {
    label: '🔒 Session Rule',
    text: 'Per security audit findings: session cookies must expire after 15 minutes of inactivity instead of 30. Compliance deadline is Friday.',
    channel: '#security',
    user: 'sec_lead',
  },
];

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats>({
    pendingReview: 0,
    newDiscoveries: 0,
    contradictions: 0,
    archived: 0,
    processedToday: 0,
  });

  const [items, setItems] = useState<ActionItem[]>([]);
  const [recentMessages, setRecentMessages] = useState<RecentMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [isConnected, setIsConnected] = useState(false);

  // Slack sim form state
  const [slackText, setSlackText] = useState('');
  const [slackChannel, setSlackChannel] = useState('#product-design');
  const [slackUser, setSlackUser] = useState('priya_pm');
  const [sending, setSending] = useState(false);

  // Hydration-safe mount flag — prevents disabled={true} vs disabled={null} mismatch
  const [mounted, setMounted] = useState(false);

  // Log feed
  const [logs, setLogs] = useState<FeedbackEntry[]>([]);
  const logId = useState(0);

  const addLog = useCallback(
    (text: string, type: FeedbackEntry['type'] = 'info') => {
      const id = Date.now();
      setLogs((prev) => [{ id, text, type }, ...prev.slice(0, 7)]);
    },
    []
  );

  const fetchData = useCallback(async () => {
    try {
      const [statsRes, actionsRes, msgRes] = await Promise.all([
        fetch(`${API_BASE}/dashboard/stats`),
        fetch(`${API_BASE}/dashboard/actions`),
        fetch(`${API_BASE}/dashboard/messages`).catch(() => null),
      ]);

      if (!statsRes.ok || !actionsRes.ok) throw new Error('API error');

      const statsData = await statsRes.json();
      const actionsData = await actionsRes.json();

      setStats(statsData);
      setItems(actionsData);
      setIsConnected(true);

      if (msgRes && msgRes.ok) {
        const msgData = await msgRes.json();
        setRecentMessages(msgData);
      }
    } catch {
      setIsConnected(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 4000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handlePushToJira = async (id: string) => {
    addLog('Approving action and queuing Jira ticket creation...', 'processing');
    try {
      const res = await fetch(`${API_BASE}/dashboard/actions/${id}/approve`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error('Approval failed');
      addLog('✓ Ticket creation queued in ghost.approval worker.', 'success');
      fetchData();
    } catch {
      addLog('✗ Failed to approve action — is the API running?', 'error');
    }
  };

  const handleDismiss = async (id: string) => {
    try {
      await fetch(`${API_BASE}/dashboard/actions/${id}/dismiss`, { method: 'POST' });
      addLog('Action dismissed and archived.', 'info');
      fetchData();
    } catch {
      addLog('✗ Dismiss failed.', 'error');
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    addLog('Syncing backlog index with text-embedding-004...', 'processing');
    try {
      const res = await fetch(`${API_BASE}/dashboard/sync`, { method: 'POST' });
      const data = await res.json();
      addLog(`✓ Backlog synced: ${data.seeded?.join(', ')}`, 'success');
      fetchData();
    } catch {
      addLog('✗ Backlog sync failed.', 'error');
    } finally {
      setSyncing(false);
    }
  };

  const handleSeedBacklog = async () => {
    setSeeding(true);
    addLog('Seeding PostgreSQL backlog_index with mock Jira tickets...', 'processing');
    try {
      const res = await fetch(`${API_BASE}/test/seed-backlog`, { method: 'POST' });
      const data = await res.json();
      addLog(`✓ Seeded: ${data.seeded_tickets?.join(', ')} (${data.successful}/${data.total})`, 'success');
      fetchData();
    } catch {
      addLog('✗ Seeding failed — check API is running on :8000', 'error');
    } finally {
      setSeeding(false);
    }
  };

  const handleSendSlack = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!slackText.trim()) return;
    setSending(true);
    addLog(`Sending to ghost.ingestion queue: "${slackText.slice(0, 40)}..."`, 'processing');
    try {
      const res = await fetch(`${API_BASE}/test/mock-slack-message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: slackText, channel: slackChannel, user: slackUser }),
      });
      const data = await res.json();
      addLog(`✓ Queued: message_id=${data.message_id?.slice(0, 8)}... (Agent 1 classifying)`, 'success');
      setSlackText('');
      fetchData();
    } catch {
      addLog('✗ Failed — is the FastAPI server running on :8000?', 'error');
    } finally {
      setSending(false);
    }
  };

  const handlePreset = (preset: (typeof PRESET_MESSAGES)[0]) => {
    setSlackText(preset.text);
    setSlackChannel(preset.channel);
    setSlackUser(preset.user);
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen flex flex-col" style={{ background: 'var(--background)' }}>
      <DashboardHeader onSync={handleSync} syncing={syncing} isConnected={isConnected} />

      <StatsBar
        pendingReview={stats.pendingReview}
        newDiscoveries={stats.newDiscoveries}
        contradictions={stats.contradictions}
        archived={stats.archived}
        processedToday={stats.processedToday}
      />

      {/* Main grid */}
      <div
        style={{
          maxWidth: '1400px',
          margin: '0 auto',
          padding: '0 24px 40px',
          width: '100%',
          display: 'flex',
          flexWrap: 'wrap',
          gap: '20px',
          flex: 1,
        }}
      >
        {/* ── Left: Developer Panel ──────────────────────────── */}
        <aside style={{ width: '320px', display: 'flex', flexDirection: 'column', gap: '16px', flexShrink: 0 }}>

          {/* Backlog Seed */}
          <div className="glass-card" style={{ padding: '20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px' }}>
              <Database style={{ width: '15px', height: '15px', color: '#6366f1' }} />
              <span style={{ fontSize: '13px', fontWeight: '700', color: '#e8ecf4' }}>
                Backlog Index
              </span>
            </div>
            <p style={{ fontSize: '12px', color: '#4d5568', marginBottom: '14px', lineHeight: 1.6 }}>
              Pre-seed PostgreSQL with mock Jira tickets and generate text-embedding-004 vectors.
            </p>
            <button
              onClick={handleSeedBacklog}
              disabled={seeding}
              className="btn-primary"
              style={{ width: '100%', justifyContent: 'center', opacity: seeding ? 0.7 : 1, cursor: seeding ? 'not-allowed' : 'pointer' }}
            >
              <Database style={{ width: '13px', height: '13px' }} />
              {seeding ? 'Generating embeddings...' : 'Seed Jira Backlog'}
            </button>
          </div>

          {/* Slack Simulator */}
          <div className="glass-card" style={{ padding: '20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
              <Terminal style={{ width: '15px', height: '15px', color: '#6366f1' }} />
              <span style={{ fontSize: '13px', fontWeight: '700', color: '#e8ecf4' }}>
                Slack Simulator
              </span>
            </div>
            <p style={{ fontSize: '12px', color: '#4d5568', marginBottom: '14px', lineHeight: 1.5 }}>
              Inject messages into the Agent 1 ingestion pipeline.
            </p>

            {/* Presets */}
            <div style={{ marginBottom: '14px' }}>
              <p className="section-label">Quick Presets</p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                {PRESET_MESSAGES.map((p, i) => (
                  <button
                    key={i}
                    onClick={() => handlePreset(p)}
                    style={{
                      background: 'rgba(255,255,255,0.03)',
                      border: '1px solid rgba(255,255,255,0.07)',
                      borderRadius: '7px',
                      padding: '7px 10px',
                      textAlign: 'left',
                      cursor: 'pointer',
                      fontSize: '11px',
                      color: '#8892a4',
                      transition: 'all 0.15s',
                      fontFamily: 'Inter, sans-serif',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = 'rgba(99,102,241,0.08)';
                      e.currentTarget.style.borderColor = 'rgba(99,102,241,0.2)';
                      e.currentTarget.style.color = '#c8d2e0';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'rgba(255,255,255,0.03)';
                      e.currentTarget.style.borderColor = 'rgba(255,255,255,0.07)';
                      e.currentTarget.style.color = '#8892a4';
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="divider" />

            {/* Manual form */}
            <form onSubmit={handleSendSlack} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div>
                <label className="section-label">Author</label>
                <input
                  type="text"
                  value={slackUser}
                  onChange={(e) => setSlackUser(e.target.value)}
                  className="input-dark"
                  placeholder="user_handle"
                />
              </div>
              <div>
                <label className="section-label">Channel</label>
                <input
                  type="text"
                  value={slackChannel}
                  onChange={(e) => setSlackChannel(e.target.value)}
                  className="input-dark"
                  placeholder="#channel-name"
                />
              </div>
              <div>
                <label className="section-label">Message</label>
                <textarea
                  rows={4}
                  value={slackText}
                  onChange={(e) => setSlackText(e.target.value)}
                  className="textarea-dark"
                  placeholder="Type a Slack message..."
                />
              </div>
              <button
                type="submit"
                disabled={mounted && (sending || !slackText.trim())}
                className="btn-primary"
                style={{
                  justifyContent: 'center',
                  opacity: mounted && (sending || !slackText.trim()) ? 0.6 : 1,
                  cursor: mounted && (sending || !slackText.trim()) ? 'not-allowed' : 'pointer',
                }}
              >
                <Send style={{ width: '13px', height: '13px' }} />
                {sending ? 'Sending...' : 'Send to Pipeline'}
              </button>
            </form>
          </div>

          {/* Pipeline Activity Log */}
          <div className="glass-card" style={{ padding: '20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <Zap style={{ width: '14px', height: '14px', color: '#f59e0b' }} />
              <span style={{ fontSize: '13px', fontWeight: '700', color: '#e8ecf4' }}>
                Activity Log
              </span>
            </div>

            {logs.length === 0 ? (
              <p style={{ fontSize: '12px', color: '#4d5568', fontFamily: 'JetBrains Mono, monospace' }}>
                {'> Waiting for activity...'}
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {logs.map((log) => (
                  <div
                    key={log.id}
                    className="animate-fade-in"
                    style={{
                      display: 'flex',
                      gap: '8px',
                      alignItems: 'flex-start',
                    }}
                  >
                    <span
                      style={{
                        color:
                          log.type === 'success'
                            ? '#10b981'
                            : log.type === 'error'
                            ? '#ef4444'
                            : log.type === 'processing'
                            ? '#f59e0b'
                            : '#6366f1',
                        fontSize: '10px',
                        flexShrink: 0,
                        fontFamily: 'JetBrains Mono, monospace',
                        marginTop: '2px',
                      }}
                    >
                      {'›'}
                    </span>
                    <span
                      style={{
                        fontSize: '11px',
                        color:
                          log.type === 'success'
                            ? '#34d399'
                            : log.type === 'error'
                            ? '#f87171'
                            : log.type === 'processing'
                            ? '#fcd34d'
                            : '#8892a4',
                        fontFamily: 'JetBrains Mono, monospace',
                        lineHeight: 1.5,
                      }}
                    >
                      {log.text}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Recent Messages */}
          {recentMessages.length > 0 && (
            <div className="glass-card" style={{ padding: '20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                <MessageSquare style={{ width: '14px', height: '14px', color: '#8892a4' }} />
                <span style={{ fontSize: '13px', fontWeight: '700', color: '#e8ecf4' }}>
                  Recent Messages
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {recentMessages.slice(0, 5).map((msg) => (
                  <div
                    key={msg.id}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '6px 8px',
                      background: 'rgba(255,255,255,0.03)',
                      borderRadius: '6px',
                    }}
                  >
                    <div>
                      <span style={{ fontSize: '11px', color: '#8892a4' }}>{msg.channel}</span>
                      <span style={{ fontSize: '10px', color: '#4d5568', marginLeft: '6px' }}>
                        @{msg.author}
                      </span>
                    </div>
                    <StatusDot status={msg.status} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </aside>

        {/* ── Right: Inbox ────────────────────────────────────── */}
        <main style={{ flex: 1, minWidth: '320px' }}>
          <div style={{ marginBottom: '16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <Inbox style={{ width: '16px', height: '16px', color: '#6366f1' }} />
              <h2 style={{ fontSize: '15px', fontWeight: '700', color: '#e8ecf4' }}>
                Requirements Inbox
              </h2>
              {items.length > 0 && (
                <span
                  style={{
                    background: 'rgba(99,102,241,0.2)',
                    border: '1px solid rgba(99,102,241,0.3)',
                    borderRadius: '20px',
                    padding: '2px 10px',
                    fontSize: '11px',
                    fontWeight: '700',
                    color: '#818cf8',
                  }}
                >
                  {items.length}
                </span>
              )}
            </div>
            <p style={{ fontSize: '11px', color: '#4d5568' }}>
              Auto-refreshes every 4s
            </p>
          </div>

          {/* ── Loading State ────── */}
          {loading ? (
            <div
              className="glass-card"
              style={{
                minHeight: '400px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '16px',
              }}
            >
              <RefreshCw
                style={{ width: '28px', height: '28px', color: '#6366f1' }}
                className="animate-spin"
              />
              <p style={{ fontSize: '14px', color: '#4d5568' }}>
                Connecting to requirement pipeline...
              </p>
            </div>
          ) : !isConnected ? (
            /* ── Offline State ─────── */
            <div
              className="glass-card"
              style={{
                minHeight: '400px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '12px',
                padding: '40px',
              }}
            >
              <div
                style={{
                  width: '52px',
                  height: '52px',
                  borderRadius: '16px',
                  background: 'rgba(239,68,68,0.1)',
                  border: '1px solid rgba(239,68,68,0.2)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  marginBottom: '8px',
                }}
              >
                <Info style={{ width: '22px', height: '22px', color: '#f87171' }} />
              </div>
              <p style={{ fontSize: '15px', fontWeight: '600', color: '#e8ecf4' }}>
                API Offline
              </p>
              <p style={{ fontSize: '13px', color: '#4d5568', textAlign: 'center', maxWidth: '340px', lineHeight: 1.6 }}>
                Cannot reach the FastAPI server at{' '}
                <code
                  style={{
                    fontFamily: 'JetBrains Mono, monospace',
                    color: '#818cf8',
                    fontSize: '12px',
                  }}
                >
                  {process.env.NEXT_PUBLIC_API_URL || 'localhost:8000'}
                </code>
                .
              </p>
              {!process.env.NEXT_PUBLIC_API_URL ? (
                <>
                  <p style={{ fontSize: '13px', color: '#4d5568', margin: '8px 0 4px 0' }}>Start with:</p>
                  <div
                    className="mono-block"
                    style={{ fontSize: '12px', width: '100%', maxWidth: '360px' }}
                  >
                    {`make run-api\nmake run-workers`}
                  </div>
                </>
              ) : (
                <p style={{ fontSize: '13px', color: '#6b7280', marginTop: '8px', textAlign: 'center' }}>
                  Please make sure your Render backend service is deployed and active.
                </p>
              )}
            </div>
          ) : items.length === 0 ? (
            /* ── Empty State ──────── */
            <div
              className="glass-card"
              style={{
                minHeight: '400px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '14px',
                padding: '48px',
                border: '1px dashed rgba(255,255,255,0.08)',
              }}
            >
              <div
                style={{
                  width: '52px',
                  height: '52px',
                  borderRadius: '16px',
                  background: 'rgba(99,102,241,0.08)',
                  border: '1px solid rgba(99,102,241,0.2)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  marginBottom: '4px',
                }}
              >
                <CheckCircle style={{ width: '22px', height: '22px', color: '#6366f1' }} />
              </div>
              <p style={{ fontSize: '15px', fontWeight: '600', color: '#e8ecf4' }}>
                Inbox Empty
              </p>
              <p
                style={{
                  fontSize: '13px',
                  color: '#4d5568',
                  textAlign: 'center',
                  maxWidth: '360px',
                  lineHeight: 1.7,
                }}
              >
                No active discoveries or contradictions. Use the{' '}
                <strong style={{ color: '#8892a4' }}>Slack Simulator</strong> to inject messages
                and watch Agent 1 classify them in real-time.
              </p>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '12px',
                  color: '#4d5568',
                }}
              >
                <span>Seed Backlog</span>
                <ArrowRight style={{ width: '12px', height: '12px' }} />
                <span>Send Message</span>
                <ArrowRight style={{ width: '12px', height: '12px' }} />
                <span>Watch cards appear</span>
              </div>
            </div>
          ) : (
            /* ── Cards Grid ──────── */
            <div className="inbox-scroll" style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {items.map((item) => (
                <RequirementCard
                  key={item.id}
                  id={item.id}
                  type={item.type}
                  slackMessage={item.slackMessage}
                  suggestedTitle={item.suggestedTitle}
                  suggestedDescription={item.suggestedDescription}
                  acceptanceCriteria={item.acceptanceCriteria}
                  conflictTicket={item.conflictTicket}
                  conflictTicketUrl={item.conflictTicketUrl}
                  conflictAnalysis={item.conflictAnalysis}
                  similarityScore={item.similarityScore}
                  isHardConstraint={item.isHardConstraint}
                  confidenceScore={item.confidenceScore}
                  sourceChannel={item.sourceChannel}
                  author={item.author}
                  createdAt={item.createdAt}
                  onPushToJira={() => handlePushToJira(item.id)}
                  onDismiss={() => handleDismiss(item.id)}
                />
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: '#10b981',
    processing: '#f59e0b',
    pending: '#6366f1',
    failed: '#ef4444',
  };
  const color = colors[status] || '#4d5568';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        fontSize: '10px',
        color,
        fontWeight: '600',
        letterSpacing: '0.05em',
        textTransform: 'uppercase',
      }}
    >
      <span
        style={{
          width: '5px',
          height: '5px',
          borderRadius: '50%',
          background: color,
          boxShadow: status === 'processing' ? `0 0 4px ${color}` : 'none',
        }}
      />
      {status}
    </span>
  );
}
