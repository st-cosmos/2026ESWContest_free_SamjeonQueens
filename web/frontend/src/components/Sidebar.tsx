import { Boxes, ChevronRight, FlaskConical, Layers, LogOut, ShoppingCart, UserPlus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { SessionUser } from '../types';

export type TabKey = 'inventory' | 'shelves' | 'orders' | 'users' | 'profile';

const NAV_ITEMS: { key: TabKey; label: string; icon: LucideIcon }[] = [
  { key: 'inventory', label: '재고 관리', icon: Boxes },
  { key: 'shelves', label: '선반 관리', icon: Layers },
  { key: 'orders', label: '주문 관리', icon: ShoppingCart },
  { key: 'users', label: '사용자 등록', icon: UserPlus },
];

interface SidebarProps {
  activeTab: TabKey;
  onSelect: (tab: TabKey) => void;
  user: SessionUser;
  onProfile: () => void;
  onLogout: () => void;
}

export default function Sidebar({ activeTab, onSelect, user, onProfile, onLogout }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="sidebar-logo-mark">
          <FlaskConical size={22} />
        </div>
        <div>
          <div className="sidebar-logo-title">Chemical Memory</div>
          <div className="sidebar-logo-sub">시약 관리 시스템</div>
        </div>
      </div>

      {NAV_ITEMS.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          className={`sidebar-nav-item${activeTab === key ? ' active' : ''}`}
          onClick={() => onSelect(key)}
        >
          <Icon size={20} />
          {label}
        </button>
      ))}

      <div className="sidebar-spacer" />

      <div className="sidebar-footer">
        <button
          type="button"
          className={`sidebar-user-card${activeTab === 'profile' ? ' active' : ''}`}
          onClick={onProfile}
          title="내 프로필 수정"
        >
          <div className="avatar">{user.nickname.charAt(0)}</div>
          <div className="sidebar-user-info">
            <div className="sidebar-user-name">{user.nickname}</div>
            <div className="sidebar-user-role">{user.role} · 내 프로필</div>
          </div>
          <ChevronRight size={16} className="sidebar-user-arrow" />
        </button>

        <button
          type="button"
          className="sidebar-logout-btn"
          onClick={onLogout}
          title="로그아웃"
        >
          <LogOut size={16} />
          <span>로그아웃</span>
        </button>
      </div>
    </aside>
  );
}

