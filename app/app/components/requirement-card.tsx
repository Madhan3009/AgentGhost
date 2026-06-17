'use client';

import { useState } from 'react';
import {
  Sparkles,
  AlertTriangle,
  Plus,
  Trash2,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Shield,
  Hash,
  User,
  CheckSquare,
} from 'lucide-react';

export type CardType = 'new_discovery' | 'contradiction_alert';

interface RequirementCardProps {
  id: string;
  type: CardType;
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
  onPushToJira?: () => void;
  onDismiss?: () => void;
}

function SimilarityPill({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    score >= 0.85 ? '#10b981' : score >= 0.65 ? '#f59e0b' : '#6366f1';

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        padding: '3px 10px',
        borderRadius: '20px',
        background: `rgba(${score >= 0.85 ? '16,185,129' : score >= 0.65 ? '245,158,11' : '99,102,241'},0.1)`,
        border: `1px solid rgba(${score >= 0.85 ? '16,185,129' : score >= 0.65 ? '245,158,11' : '99,102,241'},0.25)`,
      }}
    >
      <div
        style={{
          width: '5px',
          height: '5px',
          borderRadius: '50%',
          background: color,
          boxShadow: `0 0 6px ${color}`,
        }}
      />
      <span style={{ fontSize: '11px', fontWeight: '600', color, letterSpacing: '0.03em' }}>
        {pct}% match
      </span>
    </div>
  );
}

function MetaChip({
  icon,
  label,
  color = '#4d5568',
}: {
  icon: React.ReactNode;
  label: string;
  color?: string;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
      <span style={{ color }}>{icon}</span>
      <span style={{ fontSize: '11px', color: '#8892a4' }}>{label}</span>
    </div>
  );
}

export function RequirementCard({
  id,
  type,
  slackMessage,
  suggestedTitle,
  suggestedDescription,
  acceptanceCriteria,
  conflictTicket,
  conflictTicketUrl,
  conflictAnalysis,
  similarityScore,
  isHardConstraint,
  confidenceScore,
  sourceChannel,
  author,
  createdAt,
  onPushToJira,
  onDismiss,
}: RequirementCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [approving, setApproving] = useState(false);

  const isDiscovery = type === 'new_discovery';
  const cardClass = isDiscovery ? 'req-card-discovery' : 'req-card-contradiction';

  const handleApprove = async () => {
    setApproving(true);
    await onPushToJira?.();
    // Keep approving=true — card will disappear after next poll
  };

  return (
    <div className={`${cardClass} animate-slide-in`}>
      {/* ── Card Header ─────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {/* Type badge */}
          <div
            style={{
              width: '32px',
              height: '32px',
              borderRadius: '8px',
              background: isDiscovery
                ? 'rgba(16,185,129,0.12)'
                : 'rgba(239,68,68,0.12)',
              border: `1px solid ${isDiscovery ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)'}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            {isDiscovery ? (
              <Sparkles style={{ width: '15px', height: '15px', color: '#34d399' }} />
            ) : (
              <AlertTriangle style={{ width: '15px', height: '15px', color: '#f87171' }} />
            )}
          </div>
          <div>
            <h3
              style={{
                fontSize: '14px',
                fontWeight: '700',
                color: isDiscovery ? '#34d399' : '#f87171',
                lineHeight: 1.2,
              }}
            >
              {isDiscovery ? 'New Discovery' : 'Contradiction Alert'}
            </h3>
            <p style={{ fontSize: '11px', color: '#4d5568', marginTop: '2px' }}>
              {isDiscovery ? 'Potential untracked requirement' : 'Conflicts with existing backlog ticket'}
            </p>
          </div>
        </div>

        {/* Meta pills */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {similarityScore !== null && similarityScore !== undefined && (
            <SimilarityPill score={similarityScore} />
          )}
          {isHardConstraint && (
            <span className="badge badge-red">
              <Shield style={{ width: '9px', height: '9px' }} />
              Hard Constraint
            </span>
          )}
          {confidenceScore !== null && confidenceScore !== undefined && (
            <span className="badge badge-indigo">
              {Math.round(confidenceScore * 100)}% confidence
            </span>
          )}
        </div>
      </div>

      {/* ── Slack Message ────────────────────────────────────── */}
      <div style={{ marginBottom: '14px' }}>
        <p className="section-label">From Slack</p>
        <div
          style={{
            background: 'rgba(0,0,0,0.25)',
            border: '1px solid rgba(255,255,255,0.07)',
            borderRadius: '8px',
            padding: '12px 14px',
            fontSize: '13px',
            color: '#c8d2e0',
            lineHeight: '1.6',
            fontStyle: 'italic',
          }}
        >
          &ldquo;{slackMessage}&rdquo;
        </div>
        {/* Attribution */}
        {(sourceChannel || author) && (
          <div style={{ display: 'flex', gap: '14px', marginTop: '8px' }}>
            {sourceChannel && (
              <MetaChip
                icon={<Hash style={{ width: '10px', height: '10px' }} />}
                label={sourceChannel}
              />
            )}
            {author && (
              <MetaChip
                icon={<User style={{ width: '10px', height: '10px' }} />}
                label={author}
              />
            )}
          </div>
        )}
      </div>

      {/* ── Discovery Branch: Ticket Draft ───────────────────── */}
      {isDiscovery && suggestedTitle && (
        <div style={{ marginBottom: '14px' }}>
          <p className="section-label">Suggested Jira Ticket</p>
          <div
            style={{
              background: 'rgba(16,185,129,0.05)',
              border: '1px solid rgba(16,185,129,0.15)',
              borderRadius: '8px',
              padding: '14px',
            }}
          >
            <p
              style={{
                fontSize: '13px',
                fontWeight: '600',
                color: '#e8ecf4',
                marginBottom: '8px',
                lineHeight: 1.4,
              }}
            >
              {suggestedTitle}
            </p>
            {suggestedDescription && (
              <p
                style={{
                  fontSize: '12px',
                  color: '#8892a4',
                  lineHeight: 1.6,
                  marginBottom: acceptanceCriteria?.length ? '10px' : 0,
                }}
              >
                {suggestedDescription}
              </p>
            )}

            {/* Acceptance Criteria */}
            {acceptanceCriteria && acceptanceCriteria.length > 0 && (
              <div>
                <p
                  style={{
                    fontSize: '10px',
                    fontWeight: '700',
                    color: '#34d399',
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                    marginBottom: '6px',
                  }}
                >
                  Acceptance Criteria
                </p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                  {acceptanceCriteria.map((criterion, i) => (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        gap: '8px',
                        alignItems: 'flex-start',
                        fontSize: '12px',
                        color: '#8892a4',
                        lineHeight: 1.5,
                      }}
                    >
                      <CheckSquare
                        style={{
                          width: '12px',
                          height: '12px',
                          color: '#10b981',
                          flexShrink: 0,
                          marginTop: '2px',
                        }}
                      />
                      <span>{criterion}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Contradiction Branch: Conflict Analysis ───────────── */}
      {!isDiscovery && conflictTicket && (
        <div style={{ marginBottom: '14px' }}>
          <p className="section-label">Conflicting Ticket</p>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              background: 'rgba(239,68,68,0.06)',
              border: '1px solid rgba(239,68,68,0.2)',
              borderRadius: '8px',
              padding: '10px 14px',
            }}
          >
            <span style={{ fontSize: '13px', fontWeight: '600', color: '#fca5a5' }}>
              {conflictTicket}
            </span>
            {conflictTicketUrl && (
              <a
                href={conflictTicketUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: '#f87171', display: 'flex', alignItems: 'center', gap: '4px' }}
              >
                <ExternalLink style={{ width: '12px', height: '12px' }} />
              </a>
            )}
          </div>
        </div>
      )}

      {/* ── Expandable: Conflict Analysis ────────────────────── */}
      {!isDiscovery && conflictAnalysis && (
        <div style={{ marginBottom: '14px' }}>
          <button
            onClick={() => setExpanded(!expanded)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: '0 0 8px 0',
              color: '#8892a4',
              fontSize: '11px',
              fontWeight: '600',
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            <p className="section-label" style={{ marginBottom: 0 }}>
              Conflict Analysis & Resolution
            </p>
            {expanded ? (
              <ChevronUp style={{ width: '12px', height: '12px' }} />
            ) : (
              <ChevronDown style={{ width: '12px', height: '12px' }} />
            )}
          </button>

          {expanded && (
            <div
              style={{
                background: 'rgba(0,0,0,0.3)',
                border: '1px solid rgba(239,68,68,0.15)',
                borderRadius: '8px',
                padding: '12px 14px',
                fontSize: '12px',
                color: '#c8d2e0',
                lineHeight: 1.7,
                whiteSpace: 'pre-wrap',
                fontFamily: 'inherit',
              }}
              className="animate-fade-in"
            >
              {conflictAnalysis}
            </div>
          )}
        </div>
      )}

      <div className="divider" />

      {/* ── Action Buttons ───────────────────────────────────── */}
      <div style={{ display: 'flex', gap: '8px' }}>
        {isDiscovery ? (
          <button
            onClick={handleApprove}
            disabled={approving}
            className="btn-green"
            style={{ flex: 1, justifyContent: 'center', opacity: approving ? 0.7 : 1, cursor: approving ? 'not-allowed' : 'pointer' }}
          >
            <Plus style={{ width: '14px', height: '14px' }} />
            {approving ? 'Pushing to Jira...' : 'Push to Jira'}
          </button>
        ) : (
          <button
            onClick={handleApprove}
            disabled={approving}
            className="btn-red"
            style={{ flex: 1, justifyContent: 'center', opacity: approving ? 0.7 : 1, cursor: approving ? 'not-allowed' : 'pointer' }}
          >
            <AlertTriangle style={{ width: '14px', height: '14px' }} />
            {approving ? 'Approving...' : 'Approve Resolution'}
          </button>
        )}
        <button
          onClick={onDismiss}
          className="btn-ghost"
        >
          <Trash2 style={{ width: '13px', height: '13px' }} />
          Dismiss
        </button>
      </div>
    </div>
  );
}
