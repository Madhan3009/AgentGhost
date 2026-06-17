'use client';

import { RefreshCw, Ghost, Activity, Zap } from 'lucide-react';

interface DashboardHeaderProps {
  onSync?: () => void;
  syncing?: boolean;
  isConnected?: boolean;
}

export function DashboardHeader({ onSync, syncing, isConnected = true }: DashboardHeaderProps) {
  return (
    <header
      style={{
        background: 'rgba(19, 23, 34, 0.95)',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        backdropFilter: 'blur(20px)',
        position: 'sticky',
        top: 0,
        zIndex: 50,
      }}
    >
      <div
        style={{
          maxWidth: '1400px',
          margin: '0 auto',
          padding: '0 24px',
          height: '64px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        {/* Brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div
            style={{
              width: '36px',
              height: '36px',
              background: 'linear-gradient(135deg, #6366f1, #4f46e5)',
              borderRadius: '10px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 2px 12px rgba(99,102,241,0.4)',
              flexShrink: 0,
            }}
          >
            <Ghost style={{ width: '18px', height: '18px', color: 'white' }} />
          </div>
          <div>
            <h1
              style={{
                fontSize: '15px',
                fontWeight: '700',
                color: '#e8ecf4',
                letterSpacing: '-0.01em',
                lineHeight: 1.2,
              }}
            >
              Ghost Requirement Agent
            </h1>
            <p style={{ fontSize: '11px', color: '#4d5568', fontWeight: '400' }}>
              Autonomous requirements pipeline
            </p>
          </div>
        </div>

        {/* Center: Pipeline status */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '20px',
          }}
          className="hidden md:flex"
        >
          <PipelineStatusBadge label="Agent 1" sublabel="Ingestion" color="#10b981" active={isConnected} />
          <PipelineArrow />
          <PipelineStatusBadge label="Agent 2" sublabel="Embedding" color="#6366f1" active={isConnected} />
          <PipelineArrow />
          <PipelineStatusBadge label="Agent 3" sublabel="Resolver" color="#f59e0b" active={isConnected} />
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {/* Connection indicator */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '5px 10px',
              background: isConnected ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
              borderRadius: '20px',
              border: `1px solid ${isConnected ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)'}`,
            }}
          >
            <div
              style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background: isConnected ? '#10b981' : '#ef4444',
                boxShadow: isConnected ? '0 0 6px #10b981' : '0 0 6px #ef4444',
              }}
              className={isConnected ? 'pulse-dot' : ''}
            />
            <span
              style={{
                fontSize: '11px',
                fontWeight: '600',
                color: isConnected ? '#34d399' : '#f87171',
                letterSpacing: '0.05em',
              }}
            >
              {isConnected ? 'LIVE' : 'OFFLINE'}
            </span>
          </div>

          {/* Sync button */}
          <button
            onClick={onSync}
            disabled={syncing}
            className="btn-primary"
            style={{ opacity: syncing ? 0.7 : 1, cursor: syncing ? 'not-allowed' : 'pointer' }}
          >
            <RefreshCw
              style={{ width: '13px', height: '13px' }}
              className={syncing ? 'animate-spin' : ''}
            />
            {syncing ? 'Syncing...' : 'Sync Backlog'}
          </button>
        </div>
      </div>
    </header>
  );
}

function PipelineStatusBadge({
  label,
  sublabel,
  color,
  active,
}: {
  label: string;
  sublabel: string;
  color: string;
  active: boolean;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
      <div
        style={{
          width: '7px',
          height: '7px',
          borderRadius: '50%',
          background: active ? color : '#4d5568',
          boxShadow: active ? `0 0 8px ${color}` : 'none',
          transition: 'all 0.3s',
        }}
        className={active ? 'pulse-dot' : ''}
      />
      <div>
        <div style={{ fontSize: '11px', fontWeight: '600', color: '#8892a4', lineHeight: 1 }}>
          {label}
        </div>
        <div style={{ fontSize: '10px', color: '#4d5568', marginTop: '1px' }}>{sublabel}</div>
      </div>
    </div>
  );
}

function PipelineArrow() {
  return (
    <div
      style={{
        width: '20px',
        height: '1px',
        background: 'linear-gradient(90deg, rgba(99,102,241,0.3), rgba(99,102,241,0.6))',
        position: 'relative',
      }}
    >
      <div
        style={{
          position: 'absolute',
          right: '-1px',
          top: '-3px',
          width: '0',
          height: '0',
          borderTop: '3px solid transparent',
          borderBottom: '3px solid transparent',
          borderLeft: '5px solid rgba(99,102,241,0.6)',
        }}
      />
    </div>
  );
}
