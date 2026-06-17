'use client';

import { Clock, Sparkles, AlertTriangle, CheckCircle, Zap } from 'lucide-react';

interface StatsBarProps {
  pendingReview: number;
  newDiscoveries: number;
  contradictions: number;
  archived: number;
  processedToday?: number;
}

interface StatCardProps {
  label: string;
  value: number;
  sublabel?: string;
  icon: React.ReactNode;
  colorClass: string;
  accentColor: string;
  glowColor: string;
}

function StatCard({ label, value, sublabel, icon, colorClass, accentColor, glowColor }: StatCardProps) {
  return (
    <div
      className={`stat-card ${colorClass}`}
      style={{ cursor: 'default' }}
    >
      {/* Icon + value row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '10px' }}>
        <div
          style={{
            width: '36px',
            height: '36px',
            borderRadius: '10px',
            background: `rgba(${glowColor}, 0.12)`,
            border: `1px solid rgba(${glowColor}, 0.2)`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          {icon}
        </div>
        <div style={{ textAlign: 'right' }}>
          <div
            style={{
              fontSize: '28px',
              fontWeight: '800',
              color: accentColor,
              lineHeight: 1,
              letterSpacing: '-0.02em',
            }}
          >
            {value}
          </div>
        </div>
      </div>

      {/* Label */}
      <div>
        <p style={{ fontSize: '12px', fontWeight: '600', color: '#e8ecf4', letterSpacing: '0.01em' }}>
          {label}
        </p>
        {sublabel && (
          <p style={{ fontSize: '11px', color: '#4d5568', marginTop: '2px' }}>
            {sublabel}
          </p>
        )}
      </div>
    </div>
  );
}

export function StatsBar({
  pendingReview,
  newDiscoveries,
  contradictions,
  archived,
  processedToday = 0,
}: StatsBarProps) {
  return (
    <div
      style={{
        padding: '20px 24px',
        maxWidth: '1400px',
        margin: '0 auto',
        width: '100%',
      }}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(5, 1fr)',
          gap: '12px',
        }}
      >
        <StatCard
          label="Pending Review"
          value={pendingReview}
          sublabel="Awaiting triage"
          colorClass="blue"
          accentColor="#60a5fa"
          glowColor="59, 130, 246"
          icon={<Clock style={{ width: '16px', height: '16px', color: '#60a5fa' }} />}
        />
        <StatCard
          label="New Discoveries"
          value={newDiscoveries}
          sublabel="Untracked requirements"
          colorClass="green"
          accentColor="#34d399"
          glowColor="16, 185, 129"
          icon={<Sparkles style={{ width: '16px', height: '16px', color: '#34d399' }} />}
        />
        <StatCard
          label="Contradictions"
          value={contradictions}
          sublabel="Conflict alerts"
          colorClass="red"
          accentColor="#f87171"
          glowColor="239, 68, 68"
          icon={<AlertTriangle style={{ width: '16px', height: '16px', color: '#f87171' }} />}
        />
        <StatCard
          label="Resolved"
          value={archived}
          sublabel="Tickets created"
          colorClass="gray"
          accentColor="#9ca3af"
          glowColor="107, 114, 128"
          icon={<CheckCircle style={{ width: '16px', height: '16px', color: '#9ca3af' }} />}
        />
        <StatCard
          label="Processed Today"
          value={processedToday}
          sublabel="Messages analyzed"
          colorClass="purple"
          accentColor="#a78bfa"
          glowColor="139, 92, 246"
          icon={<Zap style={{ width: '16px', height: '16px', color: '#a78bfa' }} />}
        />
      </div>
    </div>
  );
}
