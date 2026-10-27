interface ProgressBarProps {
  /** 0~100 */
  percent: number;
  /** 트랙 폭 (재고 56px, 주문 64px) */
  trackWidth?: number;
  /** 잔량 부족 등 위험 상태 — 바·텍스트를 danger 색으로 */
  danger?: boolean;
}

export default function ProgressBar({ percent, trackWidth = 56, danger = false }: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div className={`progress${danger ? ' danger' : ''}`}>
      <div className="progress-track" style={{ width: trackWidth }}>
        <div className="progress-fill" style={{ width: `${clamped}%` }} />
      </div>
      <span className="progress-pct">{clamped}%</span>
    </div>
  );
}
