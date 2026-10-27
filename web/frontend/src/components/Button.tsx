import type { ButtonHTMLAttributes } from 'react';
import type { LucideIcon } from 'lucide-react';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Button/Web-Primary(기본) 또는 Button/Web-Outline */
  variant?: 'primary' | 'outline';
  /** 라벨 좌측 lucide 아이콘 (16px) */
  icon?: LucideIcon;
  /** 폭 100% · 48px 높이 CTA (로그인/등록 제출) */
  cta?: boolean;
  /** 컴팩트 필 버튼 (선반 관리 "새 선반 추가") */
  pill?: boolean;
  iconSize?: number;
}

export default function Button({
  variant = 'primary',
  icon: Icon,
  cta = false,
  pill = false,
  iconSize,
  className = '',
  children,
  ...rest
}: ButtonProps) {
  const classes = [
    'btn',
    `btn-${variant}`,
    cta ? 'btn-cta' : '',
    pill ? 'btn-pill' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button className={classes} {...rest}>
      {Icon && <Icon size={iconSize ?? (pill ? 15 : 16)} />}
      {children}
    </button>
  );
}
