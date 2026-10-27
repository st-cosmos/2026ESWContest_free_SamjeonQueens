import { useState } from 'react';
import type { FormEvent } from 'react';
import { Save } from 'lucide-react';
import Badge, { roleBadgeVariant } from '../components/Badge';
import Input from '../components/Input';
import { putJSON } from '../api';
import type { SessionUser, User } from '../types';

/** 웹 로그인용 비밀번호 규칙: 8자 이상, 숫자·특수문자 포함. */
function validatePassword(pw: string): string | null {
  if (pw.length < 8) return '비밀번호는 8자 이상이어야 합니다.';
  if (!/\d/.test(pw)) return '비밀번호에 숫자를 포함해야 합니다.';
  if (!/[^A-Za-z0-9]/.test(pw)) return '비밀번호에 특수문자를 포함해야 합니다.';
  return null;
}

/** 앱 로그인용 PIN 규칙: 정확히 4자리 숫자. */
function validatePin(pin: string): string | null {
  if (!/^\d{4}$/.test(pin)) return 'PIN번호는 4자리 숫자여야 합니다.';
  return null;
}

interface ProfileProps {
  user: SessionUser;
  onUpdated: (user: SessionUser) => void;
}

export default function Profile({ user, onUpdated }: ProfileProps) {
  const [nickname, setNickname] = useState(user.nickname);
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [pin, setPin] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;

    if (!nickname.trim()) {
      setNotice({ ok: false, text: '별명을 입력하세요.' });
      return;
    }
    if (password) {
      const pwError = validatePassword(password);
      if (pwError) {
        setNotice({ ok: false, text: pwError });
        return;
      }
      if (password !== passwordConfirm) {
        setNotice({ ok: false, text: '새 비밀번호가 일치하지 않습니다.' });
        return;
      }
    }
    if (pin) {
      const pinError = validatePin(pin);
      if (pinError) {
        setNotice({ ok: false, text: pinError });
        return;
      }
    }

    setSubmitting(true);
    setNotice(null);
    try {
      const updated = await putJSON<User>(`/api/users/${user.username}`, {
        nickname: nickname.trim(),
        password: password || null,
        pin: pin || null,
      });
      onUpdated({ username: updated.username, nickname: updated.nickname, role: updated.role });
      setPassword('');
      setPasswordConfirm('');
      setPin('');
      setNotice({ ok: true, text: '프로필이 저장되었습니다.' });
    } catch (err) {
      setNotice({ ok: false, text: err instanceof Error ? err.message : '저장에 실패했습니다.' });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page">
      <div className="topbar">
        <div className="topbar-info">
          <h1 className="topbar-title">내 프로필</h1>
          <span className="topbar-sub">본인 계정 정보를 확인하고 수정합니다</span>
        </div>
      </div>

      <form className="card profile-card" onSubmit={handleSubmit}>
        {/* 신원 (읽기 전용) */}
        <div className="profile-head">
          <div className="avatar avatar-lg">{user.nickname.charAt(0)}</div>
          <div className="profile-head-info">
            <div className="profile-head-name">{user.nickname}</div>
            <div className="profile-head-id">@{user.username}</div>
          </div>
          <Badge variant={roleBadgeVariant(user.role)}>{user.role}</Badge>
        </div>

        <hr className="profile-divider" />

        {/* 수정 가능 */}
        <div className="profile-edit">
          <div className="profile-section-label">정보 수정</div>

          <Input
            label="별명"
            placeholder="앱 프로필에 표시될 이름"
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
          />
          <Input
            label="새 비밀번호"
            type="password"
            placeholder="8자 이상, 숫자·특수문자 포함"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Input
            label="새 비밀번호 확인"
            type="password"
            placeholder="새 비밀번호를 다시 입력하세요"
            value={passwordConfirm}
            onChange={(e) => setPasswordConfirm(e.target.value)}
          />
          <Input
            label="새 PIN번호"
            inputMode="numeric"
            maxLength={4}
            placeholder="4자리 숫자"
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 4))}
          />

          <p className="profile-note">
            아이디와 권한은 변경할 수 없습니다. 비밀번호·PIN번호는 비워두면 기존 값이 유지됩니다.
          </p>
        </div>

        {notice && <div className={notice.ok ? 'form-success' : 'form-error'}>{notice.text}</div>}

        <button type="submit" className="btn btn-primary btn-cta" disabled={submitting}>
          <Save size={16} />
          {submitting ? '저장 중...' : '변경사항 저장'}
        </button>
      </form>
    </div>
  );
}
