import { useState } from 'react';
import type { FormEvent } from 'react';
import { ChartLine, Check, FlaskConical, Lightbulb, ScanLine } from 'lucide-react';
import Input from '../components/Input';
import { postJSON } from '../api';
import type { SessionUser, User } from '../types';

interface LoginProps {
  onLoginSuccess: (user: SessionUser) => void;
}

const FEATURES = [
  { icon: ScanLine, text: 'OCR 자동 반입·반출 기록' },
  { icon: Lightbulb, text: 'LED 위치 안내' },
  { icon: ChartLine, text: '실시간 재고·유통기한 관리' },
];

export default function Login({ onLoginSuccess }: LoginProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username || !password || loading) return;
    setLoading(true);
    setError('');
    try {
      const user = await postJSON<User>('/api/users/login', { username, password });
      onLoginSuccess({ username: user.username, nickname: user.nickname, role: user.role });
    } catch (err) {
      setError(err instanceof Error ? err.message : '로그인에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      {/* 좌측 브랜드 패널 */}
      <div className="login-brand">
        <div className="login-brand-logo">
          <div className="login-brand-mark">
            <FlaskConical size={28} />
          </div>
          <span className="login-brand-name">Chemical Memory</span>
        </div>

        <div className="login-headline">
          <h2>{'시약 반입부터 반출까지,\n선반이 스스로 관리합니다'}</h2>
          <p>{'OCR 스캔으로 기록하고, LED로 위치를 안내하는\n스마트 시약 선반 관리 시스템'}</p>
        </div>

        <div className="login-features">
          {FEATURES.map(({ icon: Icon, text }) => (
            <div key={text} className="login-feature">
              <div className="login-feature-icon">
                <Icon size={18} />
              </div>
              {text}
            </div>
          ))}
        </div>
      </div>

      {/* 우측 폼 영역 */}
      <div className="login-form-side">
        <form className="login-card" onSubmit={handleSubmit}>
          <div className="login-card-head">
            <h1>관리자 로그인</h1>
            <p>시약 입출입 내역 관리 시스템에 접속합니다</p>
          </div>

          <Input
            label="아이디"
            placeholder="아이디를 입력하세요"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
          />
          <Input
            label="비밀번호"
            type="password"
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />

          {error && <div className="form-error">{error}</div>}

          <div className="login-options">
            <button type="button" className="login-remember" onClick={() => setRemember(!remember)}>
              <span className={`checkbox${remember ? ' checked' : ''}`}>
                {remember && <Check size={12} />}
              </span>
              로그인 상태 유지
            </button>
            <button type="button" className="login-find-pw">
              비밀번호 찾기
            </button>
          </div>

          <button type="submit" className="btn btn-primary btn-cta" disabled={loading}>
            {loading ? '로그인 중...' : '로그인'}
          </button>

          <div className="login-foot">계정이 없다면 시스템 관리자에게 문의하세요</div>
        </form>
      </div>
    </div>
  );
}
