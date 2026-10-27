import { useEffect, useMemo, useState } from 'react';
import {
  ArrowDownLeft,
  ArrowUpRight,
  CalendarClock,
  ClockAlert,
  Lightbulb,
  TriangleAlert,
  X,
} from 'lucide-react';
import SearchBar from '../components/SearchBar';
import Badge from '../components/Badge';
import Button from '../components/Button';
import ProgressBar from '../components/ProgressBar';
import { getJSON, postJSON } from '../api';
import type { Alerts, Chemical, SessionUser, ShelfDevice, TransactionLog, User } from '../types';
import { useWebSocket } from '../useWebSocket';

interface InventoryProps {
  alerts: Alerts;
  refreshAlerts: () => void;
  user: SessionUser;
}

// 오늘로부터 m개월 뒤 날짜 (YYYY-MM-DD) — 유통기한 입력 기본값
function monthsFromToday(m: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() + m);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

// 잔량 % 환산: 병별 capacity_kg(라벨/사진 추정 + 실측 래칫)을 우선 쓰고,
// 미추정 병은 가득 = 500g 폴백 (시연 약품 최대 무게)
const CAPACITY_KG = 0.5;
const DANGER_PCT = 25;

// 테이블 컬럼 폭 (design-spec §4.2)
const COL = {
  weight: 90,
  location: 150,
  remain: 130,
  expiry: 125,
  status: 150,
};

// 필터 칩 (design.pen web-inventory · 앱 재고 관리와 동일한 기준)
type StatusFilter = 'all' | 'in' | 'out' | 'expiry' | 'co';

const FILTER_LABELS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: '전체' },
  { key: 'in', label: '비치중' },
  { key: 'out', label: '반출중' },
  { key: 'expiry', label: '유통기한' },
  { key: 'co', label: '인접 보관' },
];

function percentOf(weightKg: number, capacityKg?: number | null): number {
  const cap = capacityKg && capacityKg > 0 ? capacityKg : CAPACITY_KG;
  return Math.max(0, Math.min(100, Math.round((weightKg / cap) * 100)));
}

function formatLogTime(ts: string): string {
  const d = new Date(ts.includes('T') ? ts : ts.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return ts;
  const now = new Date();
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (diffDays === 0) return `오늘 ${hm}`;
  if (diffDays === 1) return `어제 ${hm}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

function timeOnly(ts: string | null): string {
  if (!ts) return '';
  const d = new Date(ts.includes('T') ? ts : ts.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return ts;
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export default function Inventory({ alerts, refreshAlerts, user }: InventoryProps) {
  const [chemicals, setChemicals] = useState<Chemical[]>([]);
  const [logs, setLogs] = useState<TransactionLog[]>([]);
  const [devices, setDevices] = useState<ShelfDevice[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [ledNotice, setLedNotice] = useState<{ ok: boolean; text: string } | null>(null);

  // 기본 유통기한 설정 (반입일 + N개월) — 관리자는 수정 가능
  const [defaultMonths, setDefaultMonths] = useState(12);
  const [monthsModalOpen, setMonthsModalOpen] = useState(false);
  const [monthsInput, setMonthsInput] = useState('12');
  const [monthsError, setMonthsError] = useState('');

  // 유통기한 입력/수정 (상세 패널) — 반입 때 자동 설정된 기본값도 여기서 고칠 수 있다
  const [expiryDraft, setExpiryDraft] = useState('');
  const [expiryEditing, setExpiryEditing] = useState(false);
  const [expirySaving, setExpirySaving] = useState(false);

  // 용량(가득 총 무게, g) 수동 수정 — 자동 추정이 틀리거나 실패한 병을 바로잡는다
  const [capDraft, setCapDraft] = useState('');
  const [capEditing, setCapEditing] = useState(false);
  const [capSaving, setCapSaving] = useState(false);

  const fetchData = async () => {
    try {
      const [chems, logList, devs] = await Promise.all([
        getJSON<Chemical[]>('/api/chemicals'),
        getJSON<TransactionLog[]>('/api/logs'),
        getJSON<ShelfDevice[]>('/api/shelves'),
      ]);
      setChemicals(chems);
      setLogs(logList);
      setDevices(devs);
    } catch {
      // 폴링 실패 무시
    } finally {
      setLoading(false);
    }
  };

  useWebSocket(() => {
    fetchData();
    refreshAlerts();
  });

  useEffect(() => {
    fetchData();
    getJSON<User[]>('/api/users')
      .then(setUsers)
      .catch(() => undefined);
    getJSON<{ months: number }>('/api/settings/default-expiry')
      .then((r) => setDefaultMonths(r.months))
      .catch(() => undefined);
    const timer = setInterval(() => {
      fetchData();
      refreshAlerts();
    }, 5000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedChem = chemicals.find((c) => c.id === selectedId) ?? null;

  // 시약 선택이 바뀌면 편집 상태를 접고 입력값을 채운다 (기존 날짜 또는 기본값)
  useEffect(() => {
    setExpiryEditing(false);
    setCapEditing(false);
    if (selectedChem) {
      setExpiryDraft(selectedChem.expiration_date ?? monthsFromToday(defaultMonths));
      setCapDraft(String(Math.round((selectedChem.capacity_kg || CAPACITY_KG) * 1000)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const saveExpiry = async () => {
    if (!selectedChem || !expiryDraft) return;
    setExpirySaving(true);
    try {
      await postJSON(`/api/chemicals/${selectedChem.id}/expiration`, {
        expiration_date: expiryDraft,
      });
      setExpiryEditing(false);
      await fetchData();
      refreshAlerts();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : '유통기한 저장에 실패했습니다.');
    } finally {
      setExpirySaving(false);
    }
  };

  const saveCapacity = async () => {
    const grams = Number(capDraft);
    if (!selectedChem || !Number.isFinite(grams) || grams <= 0) return;
    setCapSaving(true);
    try {
      await postJSON(`/api/chemicals/${selectedChem.id}/capacity`, {
        capacity_kg: grams / 1000,
      });
      setCapEditing(false);
      await fetchData();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : '용량 저장에 실패했습니다.');
    } finally {
      setCapSaving(false);
    }
  };

  const saveDefaultMonths = async () => {
    const months = Number(monthsInput);
    if (!Number.isInteger(months) || months < 1 || months > 120) {
      setMonthsError('1~120 사이의 개월 수를 입력해 주세요.');
      return;
    }
    try {
      await postJSON('/api/settings/default-expiry', { months });
      setDefaultMonths(months);
      setMonthsModalOpen(false);
    } catch (err) {
      setMonthsError(err instanceof Error ? err.message : '설정 저장에 실패했습니다.');
    }
  };

  const nicknameOf = useMemo(() => {
    const map = new Map(users.map((u) => [u.username, u.nickname]));
    return (username: string | null) => (username ? map.get(username) ?? username : '');
  }, [users]);

  const shelfLetterOf = useMemo(() => {
    const map = new Map(devices.map((d) => [d.id, d.parent_shelf]));
    return (shelfId: string | null) => (shelfId ? map.get(shelfId) ?? null : null);
  }, [devices]);

  const locationText = (chem: Chemical): string => {
    const letter = shelfLetterOf(chem.shelf_id);
    if (letter && chem.shelf_row != null && chem.shelf_col != null) {
      return `${letter} · ${chem.shelf_row}행 ${chem.shelf_col}열`;
    }
    if (chem.shelf_row != null && chem.shelf_col != null) {
      return `${chem.shelf_row}행 ${chem.shelf_col}열`;
    }
    return '-';
  };

  const expiredAlertOf = (id: string) => alerts.expired_chemicals.find((e) => e.chemical_id === id);
  const coAlertOf = (id: string) =>
    alerts.co_storage_warnings.find((c) => c.chemical_1_id === id || c.chemical_2_id === id);

  const alertCount =
    alerts.expired_chemicals.length +
    alerts.co_storage_warnings.length +
    alerts.unscanned_checkouts.length;

  // 필터 대상 시약 id 집합 — 유통기한(초과+임박), 인접 보관(경고 쌍 양쪽)
  const expiredIds = new Set(alerts.expired_chemicals.map((e) => e.chemical_id));
  const coIds = new Set(
    alerts.co_storage_warnings.flatMap((c) => [c.chemical_1_id, c.chemical_2_id]),
  );

  const matchesKey = (c: Chemical, key: StatusFilter): boolean => {
    switch (key) {
      case 'in':
        return c.current_status !== '반출중';
      case 'out':
        return c.current_status === '반출중';
      case 'expiry':
        return expiredIds.has(c.id);
      case 'co':
        return coIds.has(c.id);
      default:
        return true;
    }
  };

  const filterCount = (key: StatusFilter): number =>
    chemicals.filter((c) => matchesKey(c, key)).length;

  const filtered = chemicals.filter((c) => {
    if (!matchesKey(c, filter)) return false;
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      c.name.toLowerCase().includes(q) ||
      (c.formula ?? '').toLowerCase().includes(q) ||
      (c.cas_no ?? '').toLowerCase().includes(q)
    );
  });

  const selected = chemicals.find((c) => c.id === selectedId) ?? null;
  const selectedLogs = logs.filter((l) => l.chemical_id === selectedId);

  const handleLed = async (chem: Chemical) => {
    try {
      const res = await postJSON<{
        status: string;
        shelf_id: string;
        chemical_name: string;
        parent_shelf: string;
        row: number;
        col: number;
      }>('/api/chemicals/select-led', { chem_id: chem.id });
      setLedNotice({
        ok: true,
        text: `${res.chemical_name} 위치 LED 점등 — 선반 ${res.parent_shelf} · ${res.row}행 ${res.col}열`,
      });
    } catch (err) {
      setLedNotice({
        ok: false,
        text: err instanceof Error ? err.message : 'LED 점등에 실패했습니다.',
      });
    }
    setTimeout(() => setLedNotice(null), 4000);
  };

  return (
    <div className="inventory-layout">
      <div className="page">
        <div className="topbar">
          <div className="topbar-info">
            <h1 className="topbar-title">재고 관리</h1>
            <span className="topbar-sub">
              보유 시약 {chemicals.length}종 · 경고 {alertCount}건 · 마지막 동기화 방금 전
            </span>
          </div>
          <div className="topbar-actions">
            {user.role === '관리자' && (
              <button
                className="expiry-setting-btn"
                title="유통기한 미인식 시 입력 기본값 설정"
                onClick={() => {
                  setMonthsInput(String(defaultMonths));
                  setMonthsError('');
                  setMonthsModalOpen(true);
                }}
              >
                <CalendarClock size={16} />
                기본 유통기한 {defaultMonths}개월
              </button>
            )}
            <SearchBar value={search} onChange={setSearch} placeholder="시약 이름·CAS 번호 검색" />
          </div>
        </div>

        {/* 상태 필터 칩 (design.pen web-inventory) */}
        <div className="filter-chips">
          {FILTER_LABELS.map(({ key, label }) => (
            <button
              key={key}
              className={`filter-chip${filter === key ? ' active' : ''}`}
              onClick={() => setFilter(key)}
            >
              {label} {filterCount(key)}
            </button>
          ))}
        </div>

        <div className="card table-card">
          <div className="t-head">
            <div className="t-cell grow">시약 이름</div>
            <div className="t-cell" style={{ width: COL.weight }}>무게</div>
            <div className="t-cell" style={{ width: COL.location }}>위치</div>
            <div className="t-cell" style={{ width: COL.remain }}>잔량</div>
            <div className="t-cell" style={{ width: COL.expiry }}>유통기한</div>
            <div className="t-cell" style={{ width: COL.status }}>상태</div>
          </div>
          <div className="t-body">
            {loading ? (
              <div className="t-empty">데이터를 불러오는 중입니다...</div>
            ) : filtered.length === 0 ? (
              <div className="t-empty">검색 결과가 없습니다.</div>
            ) : (
              filtered.map((chem) => {
                const pct = percentOf(chem.weight, chem.capacity_kg);
                const danger = pct <= DANGER_PCT;
                const expired = expiredAlertOf(chem.id);
                const co = coAlertOf(chem.id);
                const isOut = chem.current_status === '반출중';
                const expiryClass = expired
                  ? expired.days_over > 0
                    ? ' exp-danger'
                    : ' exp-warning'
                  : '';
                return (
                  <div
                    key={chem.id}
                    className={`t-row clickable${selectedId === chem.id ? ' selected' : ''}`}
                    onClick={() => setSelectedId(chem.id)}
                  >
                    <div className="t-cell grow">
                      <div className="cell-title">
                        <span className="cell-title-main">{chem.name}</span>
                        <span className="cell-title-sub">
                          {chem.formula ? `${chem.formula} · ` : ''}CAS {chem.cas_no ?? '-'}
                        </span>
                      </div>
                    </div>
                    <div className="t-cell" style={{ width: COL.weight }}>
                      {chem.weight.toFixed(1)} kg
                    </div>
                    <div className="t-cell" style={{ width: COL.location }}>
                      <div className="loc-cell" title={co?.message}>
                        {locationText(chem)}
                        {co && <TriangleAlert size={15} />}
                      </div>
                    </div>
                    <div className="t-cell" style={{ width: COL.remain }}>
                      <ProgressBar percent={pct} trackWidth={56} danger={danger} />
                    </div>
                    <div className="t-cell" style={{ width: COL.expiry }}>
                      <div className={`exp-cell${expiryClass}`}>
                        {chem.expiration_date ?? <span className="exp-missing">미입력</span>}
                        {expired && <ClockAlert size={15} />}
                      </div>
                    </div>
                    <div className="t-cell" style={{ width: COL.status }}>
                      {isOut ? (
                        <Badge variant="danger">반출 · {nicknameOf(chem.holder_username)}</Badge>
                      ) : (
                        <Badge variant="success">비치중</Badge>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* 우측 상세 패널 */}
      {selected && (
        <aside className="detail-panel">
          <div className="detail-head">
            <div className="detail-head-title">
              <h2>{selected.name}</h2>
              <p>
                {selected.formula ? `${selected.formula} · ` : ''}CAS {selected.cas_no ?? '-'}
              </p>
            </div>
            <button className="detail-close" onClick={() => setSelectedId(null)} title="닫기">
              <X size={20} />
            </button>
          </div>

          <div className="detail-badges">
            {selected.current_status === '반출중' ? (
              <Badge variant="danger">반출중</Badge>
            ) : (
              <Badge variant="success">비치중</Badge>
            )}
            {(() => {
              const expired = expiredAlertOf(selected.id);
              if (!expired) return null;
              return expired.days_over > 0 ? (
                <Badge variant="danger">유통기한 초과</Badge>
              ) : (
                <Badge variant="warning">유통기한 임박</Badge>
              );
            })()}
            {coAlertOf(selected.id) && <Badge variant="warning">인접 보관 주의</Badge>}
          </div>

          <div className="info-card">
            <div className="info-row">
              <span className="info-key">무게</span>
              <span className="info-val">{selected.weight.toFixed(1)} kg</span>
            </div>
            <div className="info-row">
              <span className="info-key">보관 위치</span>
              <span className="info-val">선반 {locationText(selected)}</span>
            </div>
            <div className="info-row">
              <span className="info-key">잔량</span>
              {!capEditing ? (
                <span className="info-val expiry-val">
                  {percentOf(selected.weight, selected.capacity_kg)}% (
                  {Math.round(selected.weight * 1000)}g / 가득{' '}
                  {Math.round((selected.capacity_kg || CAPACITY_KG) * 1000)}g)
                  <button
                    className="expiry-edit-link"
                    title="가득 무게 수정 (자동 추정이 틀렸을 때 바로잡기)"
                    onClick={() => {
                      setCapDraft(
                        String(Math.round((selected.capacity_kg || CAPACITY_KG) * 1000)),
                      );
                      setCapEditing(true);
                    }}
                  >
                    수정
                  </button>
                </span>
              ) : (
                <span className="expiry-editor">
                  <input
                    type="number"
                    className="expiry-date-input"
                    style={{ width: 90 }}
                    min={50}
                    step={10}
                    value={capDraft}
                    onChange={(e) => setCapDraft(e.target.value)}
                  />
                  <span style={{ alignSelf: 'center' }}>g</span>
                  <button
                    className="expiry-save-btn"
                    disabled={capSaving || !capDraft || Number(capDraft) <= 0}
                    onClick={saveCapacity}
                  >
                    저장
                  </button>
                </span>
              )}
            </div>
            <div className="info-row">
              <span className="info-key">유통기한</span>
              {selected.expiration_date && !expiryEditing ? (
                <span className="info-val expiry-val">
                  {selected.expiration_date}
                  <button
                    className="expiry-edit-link"
                    title="유통기한 수정 (반입 때 자동 설정된 기본값도 수정 가능)"
                    onClick={() => {
                      setExpiryDraft(selected.expiration_date ?? monthsFromToday(defaultMonths));
                      setExpiryEditing(true);
                    }}
                  >
                    수정
                  </button>
                </span>
              ) : (
                <span className="expiry-editor">
                  <input
                    type="date"
                    className="expiry-date-input"
                    value={expiryDraft}
                    onChange={(e) => setExpiryDraft(e.target.value)}
                  />
                  <button
                    className="expiry-save-btn"
                    disabled={expirySaving || !expiryDraft}
                    onClick={saveExpiry}
                  >
                    저장
                  </button>
                </span>
              )}
            </div>
            <div className="info-row">
              <span className="info-key">현재 상태</span>
              <span className="info-val">
                {selected.current_status === '반출중'
                  ? `반출중 · ${nicknameOf(selected.holder_username)}${
                      selected.time_out ? ` (${timeOnly(selected.time_out)})` : ''
                    }`
                  : '비치중'}
              </span>
            </div>
            {selected.incompatible_chemicals && (
              <div className="info-row column">
                <span className="info-key">혼재 금지 시약 (LLM 분석)</span>
                <span className="info-val warning-text">
                  {(() => {
                    try {
                      const list = JSON.parse(selected.incompatible_chemicals);
                      return Array.isArray(list) ? list.join(', ') : selected.incompatible_chemicals;
                    } catch {
                      return selected.incompatible_chemicals;
                    }
                  })()}
                </span>
                {selected.incompatible_reason && (
                  <span className="info-sub-text">{selected.incompatible_reason}</span>
                )}
              </div>
            )}
          </div>

          {coAlertOf(selected.id) && (
            <div className="co-alert-card">
              <div className="co-alert-header">
                <TriangleAlert size={18} />
                <span>인접 보관 위험 경고</span>
              </div>
              <div className="co-alert-body">
                {coAlertOf(selected.id)?.message}
                {coAlertOf(selected.id)?.reason && (
                  <p className="co-alert-reason">💡 {coAlertOf(selected.id)?.reason}</p>
                )}
              </div>
            </div>
          )}

          <Button
            icon={Lightbulb}
            onClick={() => handleLed(selected)}
            disabled={selected.current_status === '반출중'}
          >
            LED 점등
          </Button>
          {ledNotice && (
            <div className={ledNotice.ok ? 'form-success' : 'form-error'}>{ledNotice.text}</div>
          )}

          <div className="log-section">
            <span className="log-section-title">반출입 로그</span>
            {selectedLogs.length === 0 ? (
              <span className="log-item-time">로그 기록이 없습니다.</span>
            ) : (
              selectedLogs.map((log) => {
                const isOut = log.action === '반출';
                return (
                  <div key={log.id} className="log-item">
                    <div className={`log-chip ${isOut ? 'out' : 'in'}`}>
                      {isOut ? <ArrowUpRight size={15} /> : <ArrowDownLeft size={15} />}
                    </div>
                    <div className="log-item-text">
                      <span className="log-item-main">
                        {log.action} · {log.operator_name}
                      </span>
                      <span className="log-item-time">{formatLogTime(log.timestamp)}</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </aside>
      )}

      {/* 기본 유통기한 설정 모달 (관리자) */}
      {monthsModalOpen && (
        <div className="modal-overlay" onClick={() => setMonthsModalOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">기본 유통기한 설정</h3>
            <span className="modal-sub">
              라벨에서 유통기한을 인식하지 못한 시약에 자동으로 설정되는 유통기한입니다.
              (반입일로부터 N개월 뒤 — 개별 시약은 상세 패널에서 수정 가능)
            </span>
            <div className="input-group">
              <span className="input-label">반입일로부터 (개월)</span>
              <input
                className="input-field"
                type="number"
                min={1}
                max={120}
                value={monthsInput}
                onChange={(e) => setMonthsInput(e.target.value)}
              />
            </div>
            {monthsError && <div className="form-error">{monthsError}</div>}
            <div className="modal-actions">
              <Button type="button" variant="outline" onClick={() => setMonthsModalOpen(false)}>
                취소
              </Button>
              <Button type="button" onClick={saveDefaultMonths}>
                저장
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
