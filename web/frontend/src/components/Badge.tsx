import type { ReactNode } from 'react';

export type BadgeVariant = 'success' | 'danger' | 'warning' | 'primary';

interface BadgeProps {
  variant: BadgeVariant;
  children: ReactNode;
}

export default function Badge({ variant, children }: BadgeProps) {
  return <span className={`badge badge-${variant}`}>{children}</span>;
}

/** 사용자 권한 → 뱃지 변형 매핑 (관리자=primary, 연구원=success) */
export function roleBadgeVariant(role: string): BadgeVariant {
  switch (role) {
    case '관리자':
      return 'primary';
    case '연구원':
      return 'success';
    default:
      return 'primary';
  }
}
