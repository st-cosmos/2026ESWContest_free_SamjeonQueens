import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Ellipsis, Trash2, UserPlus } from 'lucide-react';
import Badge, { roleBadgeVariant } from '../components/Badge';
import Input from '../components/Input';
import SearchBar from '../components/SearchBar';
import { deleteJSON, getJSON, postJSON } from '../api';
import type { User } from '../types';

const ROLES = ['관리자', '연구원'] as const;

function formatDate(iso: string): string {
  return iso ? iso.slice(0, 10) : '-';
}

/** 웹 로그인용 비밀번호 규칙: 8자 이상, 숫자·특수문자 포함. 문제 있으면 메시지를, 없으면 null 을 반환. */
function validatePassword(pw: string): string | null {
  if (pw.length < 8) return '비밀번호는 8자 이상이어야 합니다.';
  if (!/\d/.test(pw)) return '비밀번호에 숫자를 포함해야 합니다.';
  if (!/[^A-Za-z0-9]/.test(pw)) return '비밀번호에 특수문자를 포함해야 합니다.';
  return null;
}

/** 아이디 규칙: 영문·숫자 4자 이상. */
function validateUsername(username: string): string | null {
  const trimmed = username.trim();
  if (trimmed.length < 4) return '아이디는 4자 이상이어야 합니다.';
  if (!/^[a-zA-Z0-9._-]+$/.test(trimmed)) return '아이디는 영문, 숫자 및 특수기호(._-)만 사용할 수 있습니다.';
  return null;
}

/** 앱 로그인용 PIN 규칙: 정확히 4자리 숫자. */
function validatePin(pin: string): string | null {
  if (!/^\d{4}$/.test(pin)) return 'PIN번호는 4자리 숫자여야 합니다.';
  return null;
}

export default function UserRegister() {
  const [users, setUsers] = useState<User[]>([]);
  const [search, setSearch] = useState('');

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [nickname, setNickname] = useState('');
  const [pin, setPin] = useState('');
  const [role, setRole] = useState<string>('연구원');
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const [openMenuUsername, setOpenMenuUsername] = useState<string | null>(null);

  const fetchUsers = async () => {
    try {
      setUsers(await getJSON<User[]>('/api/users'));
    } catch {
      // 무시
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (openMenuUsername && !(e.target as HTMLElement).closest('.user-row-more-container')) {
        setOpenMenuUsername(null);
      }
    };
    window.addEventListener('click', handleClickOutside);
    return () => window.removeEventListener('click', handleClickOutside);
  }, [openMenuUsername]);

  const handleDeleteUser = async (targetUsername: string, targetNickname: string) => {
    setOpenMenuUsername(null);
    if (!window.confirm(`'${targetNickname} (@${targetUsername})' 사용자를 삭제하시겠습니까?`)) {
      return;
    }
    try {
      await deleteJSON(`/api/users/${targetUsername}`);
      setNotice({ ok: true, text: `'${targetNickname}' 계정이 삭제되었습니다.` });
      await fetchUsers();
    } catch (err) {
      setNotice({ ok: false, text: err instanceof Error ? err.message : '삭제에 실패했습니다.' });
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    const cleanUsername = username.trim();
    if (!cleanUsername || !password || !passwordConfirm || !nickname.trim() || !pin) {
      setNotice({ ok: false, text: '아이디·비밀번호·비밀번호 확인·별명·PIN번호를 모두 입력하세요.' });
      return;
    }
    const usernameError = validateUsername(cleanUsername);
    if (usernameError) {
      setNotice({ ok: false, text: usernameError });
      return;
    }
    const pwError = validatePassword(password);
    if (pwError) {
      setNotice({ ok: false, text: pwError });
      return;
    }
    if (password !== passwordConfirm) {
      setNotice({ ok: false, text: '비밀번호가 일치하지 않습니다.' });
      return;
    }
    const pinError = validatePin(pin);
    if (pinError) {
      setNotice({ ok: false, text: pinError });
      return;
    }
    setSubmitting(true);
    setNotice(null);
    try {
      const created = await postJSON<User>('/api/users/register', {
        username: cleanUsername,
        password,
        nickname: nickname.trim(),
        pin,
        role,
      });
      setNotice({ ok: true, text: `${created.nickname} (${created.role}) 계정이 생성되었습니다.` });
      setUsername('');
      setPassword('');
      setPasswordConfirm('');
      setNickname('');
      setPin('');
      setRole('연구원');
      await fetchUsers();
    } catch (err) {
      setNotice({ ok: false, text: err instanceof Error ? err.message : '등록에 실패했습니다.' });
    } finally {
      setSubmitting(false);
    }
  };

  const filtered = users.filter((u) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return u.nickname.toLowerCase().includes(q) || u.username.toLowerCase().includes(q);
  });

  const usernameErrorText = username.length > 0 ? (validateUsername(username) || undefined) : undefined;
  const passwordConfirmErrorText =
    passwordConfirm.length > 0 && password !== passwordConfirm ? '비밀번호가 일치하지 않습니다.' : undefined;

  return (
    <div className="page">
      <div className="topbar">
        <div className="topbar-info">
          <h1 className="topbar-title">사용자 등록</h1>
          <span className="topbar-sub">태블릿 앱과 웹에서 사용할 계정을 생성합니다</span>
        </div>
      </div>

      <div className="register-layout">
        {/* 좌: 등록 폼 카드 */}
        <form className="card form-card" onSubmit={handleSubmit}>
          <h3 className="form-card-title">새 사용자 정보</h3>

          <Input
            label="아이디"
            placeholder="영문·숫자 4자 이상"
            minLength={4}
            value={username}
            error={usernameErrorText}
            onChange={(e) => setUsername(e.target.value)}
          />
          <Input
            label="비밀번호"
            type="password"
            placeholder="8자 이상, 숫자·특수문자 포함"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Input
            label="비밀번호 확인"
            type="password"
            placeholder="비밀번호 재입력"
            value={passwordConfirm}
            error={passwordConfirmErrorText}
            onChange={(e) => setPasswordConfirm(e.target.value)}
          />
          <Input
            label="별명"
            placeholder="앱 프로필에 표시될 이름"
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
          />
          <Input
            label="PIN번호"
            placeholder="4자리 숫자"
            inputMode="numeric"
            maxLength={4}
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 4))}
          />

          <div className="input-group">
            <span className="input-label">권한</span>
            <div className="segment">
              {ROLES.map((r) => (
                <button
                  key={r}
                  type="button"
                  className={role === r ? 'active' : ''}
                  onClick={() => setRole(r)}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>

          {notice && <div className={notice.ok ? 'form-success' : 'form-error'}>{notice.text}</div>}

          <button type="submit" className="btn btn-primary btn-cta" disabled={submitting}>
            <UserPlus size={16} />
            {submitting ? '등록 중...' : '사용자 등록'}
          </button>
        </form>

        {/* 우: 사용자 목록 카드 */}
        <div className="card user-list-card">
          <div className="user-list-head">
            <h3>등록된 사용자 {users.length}명</h3>
            <SearchBar value={search} onChange={setSearch} placeholder="사용자 검색" width={220} />
          </div>
          <div className="user-list-body">
            {filtered.length === 0 ? (
              <div className="t-empty">표시할 사용자가 없습니다.</div>
            ) : (
              filtered.map((u) => (
                <div key={u.username} className="user-row">
                  <div className="avatar avatar-sm">{u.nickname.charAt(0)}</div>
                  <div className="user-row-info">
                    <span className="user-row-name">{u.nickname}</span>
                    <span className="user-row-id">@{u.username}</span>
                  </div>
                  <Badge variant={roleBadgeVariant(u.role)}>{u.role}</Badge>
                  <span className="user-row-date">등록일 {formatDate(u.created_at)}</span>
                  <div className="user-row-more-container">
                    <button
                      type="button"
                      className="user-row-more"
                      title="더보기"
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpenMenuUsername(openMenuUsername === u.username ? null : u.username);
                      }}
                    >
                      <Ellipsis size={18} />
                    </button>
                    {openMenuUsername === u.username && (
                      <div className="user-menu-dropdown">
                        <button
                          type="button"
                          className="user-menu-item"
                          onClick={() => handleDeleteUser(u.username, u.nickname)}
                        >
                          <Trash2 size={15} />
                          <span>사용자 삭제</span>
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
