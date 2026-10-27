import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, FormEvent, UIEvent } from 'react';
import {
  BatteryFull,
  Check,
  CirclePlus,
  FlaskConical,
  HardDrive,
  Lightbulb,
  Pencil,
  Plus,
  Trash2,
  X,
} from 'lucide-react';
import Badge from '../components/Badge';
import Button from '../components/Button';
import Input from '../components/Input';
import { deleteJSON, getJSON, postJSON } from '../api';
import type { Chemical, ShelfConfig, ShelfDevice } from '../types';
import { useWebSocket } from '../useWebSocket';

interface RegisterTarget {
  shelfId: string;
  row: number;
  col: number;
}

interface ShelfCardProps {
  config: ShelfConfig;
  devices: ShelfDevice[];
  chemicals: Chemical[];
  hasUnregistered: boolean;
  editing: boolean;
  selectedSlotId: string | null;
  onToggleEdit: () => void;
  onDeleteShelf: () => void;
  onOpenRegister: (t: RegisterTarget) => void;
  onOpenSlot: (device: ShelfDevice) => void;
  refresh: () => Promise<void>;
}

// design.pen web-shelf-manage / web-shelf-manage-edit 프레임 기준.
// 수정 모드(연필)에서만 행/열 추가·삭제, 셀 등록 해제가 노출된다.
function ShelfCard({
  config,
  devices,
  chemicals,
  hasUnregistered,
  editing,
  selectedSlotId,
  onToggleEdit,
  onDeleteShelf,
  onOpenRegister,
  onOpenSlot,
  refresh,
}: ShelfCardProps) {
  const colStripRef = useRef<HTMLDivElement>(null);
  const rowDelRef = useRef<HTMLDivElement>(null);

  // 그리드 스크롤 시 열/행 삭제 버튼 줄도 함께 이동
  const syncScroll = (e: UIEvent<HTMLDivElement>) => {
    if (colStripRef.current) colStripRef.current.scrollLeft = e.currentTarget.scrollLeft;
    if (rowDelRef.current) rowDelRef.current.scrollTop = e.currentTarget.scrollTop;
  };

  const updateConfig = async (body: { rows?: number; cols?: number }) => {
    try {
      await postJSON(`/api/shelves/configs/${config.id}`, body);
      await refresh();
    } catch {
      // 무시
    }
  };

  const unregisterDevice = async (device: ShelfDevice) => {
    const ok = window.confirm(
      `${device.id} 기기의 등록을 해제할까요?\n게이트웨이에 연결되어 있으면 신규 등록 대기 목록에 다시 나타납니다.`,
    );
    if (!ok) return;
    try {
      await postJSON(`/api/shelves/unregister/${device.id}`);
      await refresh();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : '등록 해제에 실패했습니다.');
    }
  };

  const registeredIn = (pred: (d: ShelfDevice) => boolean) =>
    devices.filter((d) => d.status === 'registered' && d.parent_shelf === config.id && pred(d));

  const deleteRow = async (row: number) => {
    const cnt = registeredIn((d) => d.row === row).length;
    const ok = window.confirm(
      `${row}행을 삭제할까요?${cnt > 0 ? `\n행에 등록된 기기 ${cnt}대도 함께 등록 해제됩니다.` : ''}`,
    );
    if (!ok) return;
    try {
      await postJSON(`/api/shelves/configs/${config.id}/delete-row`, { row });
      await refresh();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : '행 삭제에 실패했습니다.');
    }
  };

  const deleteCol = async (col: number) => {
    const cnt = registeredIn((d) => d.col === col).length;
    const ok = window.confirm(
      `${col}열을 삭제할까요?${cnt > 0 ? `\n열에 등록된 기기 ${cnt}대도 함께 등록 해제됩니다.` : ''}`,
    );
    if (!ok) return;
    try {
      await postJSON(`/api/shelves/configs/${config.id}/delete-col`, { col });
      await refresh();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : '열 삭제에 실패했습니다.');
    }
  };

  return (
    <div className="shelf-card">
      <div className="shelf-card-head">
        <div className="shelf-card-head-left">
          <span className="shelf-card-title">선반 {config.id}</span>
          <span className="shelf-size-pill">
            {config.rows}행 × {config.cols}열
          </span>
        </div>
        {editing ? (
          <div className="shelf-head-actions">
            <button className="shelf-delete-btn" onClick={onDeleteShelf}>
              <Trash2 size={16} />
              선반 삭제
            </button>
            <button className="shelf-edit-done" onClick={onToggleEdit}>
              <Check size={16} />
              완료
            </button>
          </div>
        ) : (
          <button className="shelf-edit-btn" title="선반 편집" onClick={onToggleEdit}>
            <Pencil size={24} />
          </button>
        )}
      </div>

      {/* 열 삭제 버튼 줄 — 그리드 뷰포트와 같은 폭으로 클리핑, 가로 스크롤 동기화 */}
      {editing && (
        <div className="col-del-row">
          <div className="col-del-clip" ref={colStripRef}>
            <div className="shelf-grid" style={{ '--cols': config.cols } as CSSProperties}>
              {Array.from({ length: config.cols }, (_, i) => (
                <div key={i} className="line-del-cell">
                  <button
                    className="line-del-btn"
                    title={`${i + 1}열 삭제`}
                    onClick={() => deleteCol(i + 1)}
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="shelf-grid-wrap">
        {/* 3행만 보이는 뷰포트 — 그 이상은 세로 스크롤로 노출 (선반 카드 높이 통일) */}
        <div className="shelf-grid-viewport" onScroll={editing ? syncScroll : undefined}>
          {config.rows * config.cols === 0 && (
            <div className="shelf-grid-empty">
              {editing
                ? '아래 [행 추가]·[열 추가] 버튼으로 수납칸을 만드세요'
                : '빈 선반입니다 — 연필 아이콘을 눌러 행과 열을 추가하세요'}
            </div>
          )}
          <div className="shelf-grid" style={{ '--cols': config.cols } as CSSProperties}>
            {Array.from({ length: config.rows * config.cols }, (_, idx) => {
              const row = Math.floor(idx / config.cols) + 1;
              const col = (idx % config.cols) + 1;
              const device = devices.find(
                (d) =>
                  d.status === 'registered' &&
                  d.parent_shelf === config.id &&
                  d.row === row &&
                  d.col === col,
              );

              if (device) {
                const slotChems = chemicals.filter(
                  (c) => c.shelf_id === device.id && c.current_status === '비치중',
                );
                return (
                  <div
                    key={idx}
                    className={`slot slot-occupied${!editing ? ' slot-clickable' : ''}${
                      selectedSlotId === device.id ? ' slot-selected' : ''
                    }`}
                    title={editing ? undefined : '클릭하여 비치 시약 보기'}
                    onClick={editing ? undefined : () => onOpenSlot(device)}
                  >
                    {editing && (
                      <button
                        className="slot-del-btn"
                        title="기기 등록 해제"
                        onClick={() => unregisterDevice(device)}
                      >
                        <X size={14} />
                      </button>
                    )}
                    <div className="slot-top-row">
                      <div className="slot-top-left">
                        <div className="slot-chem-row">
                          {/* 슬롯 제목 = 기기 등록 시 작성한 식별 이름 (시약 내역은 클릭 모달에서) */}
                          <span className="slot-chem-name">{device.name ?? device.id}</span>
                          {slotChems.length === 0 && (
                            <span className="slot-chem-more">비어 있음</span>
                          )}
                        </div>
                        <span className="slot-pos">
                          {config.id}
                          {(row - 1) * config.cols + col} · {device.id}
                        </span>
                      </div>
                      {/* 수정 모드에서는 우상단이 등록 해제 배지 자리 — 배터리 숨김 */}
                      {!editing && (
                        <div className="slot-top-right">
                          {device.led_on && (
                            <span className="slot-meta-item led" title="LED 점등 중">
                              <Lightbulb size={15} />
                            </span>
                          )}
                          <span
                            className={`slot-meta-item battery${device.battery <= 20 ? ' low' : ''}`}
                          >
                            <BatteryFull size={15} />
                            {device.battery}%
                          </span>
                        </div>
                      )}
                    </div>
                    <div className="slot-info">
                      <span className="slot-remain-val">{device.weight.toFixed(1)}kg</span>
                    </div>
                  </div>
                );
              }

              // 수정 모드에서는 빈 슬롯을 배치 타깃으로 쓰지 않는다
              if (!editing && hasUnregistered) {
                return (
                  <div
                    key={idx}
                    className="slot slot-drop"
                    onClick={() => onOpenRegister({ shelfId: config.id, row, col })}
                  >
                    <CirclePlus size={32} />
                    여기에 배치
                  </div>
                );
              }

              return (
                <div key={idx} className="slot slot-empty">
                  빈 슬롯
                </div>
              );
            })}
          </div>
        </div>

        {/* 행 삭제 버튼 레일 — 카드 왼쪽 여백에 겹쳐 수납칸 크기에 영향 없음, 세로 스크롤 동기화 */}
        {editing && (
          <div className="row-del-col" ref={rowDelRef}>
            {Array.from({ length: config.rows }, (_, i) => (
              <div key={i} className="row-del-cell">
                <button
                  className="line-del-btn"
                  title={`${i + 1}행 삭제`}
                  onClick={() => deleteRow(i + 1)}
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {editing && (
        <div className="add-line-row">
          <button className="add-line-btn" onClick={() => updateConfig({ rows: config.rows + 1 })}>
            <Plus size={16} />행 추가
          </button>
          <button className="add-line-btn" onClick={() => updateConfig({ cols: config.cols + 1 })}>
            <Plus size={16} />열 추가
          </button>
        </div>
      )}
    </div>
  );
}

export default function Shelves() {
  const [devices, setDevices] = useState<ShelfDevice[]>([]);
  const [configs, setConfigs] = useState<ShelfConfig[]>([]);
  const [chemicals, setChemicals] = useState<Chemical[]>([]);
  const [loading, setLoading] = useState(true);
  const [editShelfId, setEditShelfId] = useState<string | null>(null);

  const [target, setTarget] = useState<RegisterTarget | null>(null);
  const [deviceId, setDeviceId] = useState('');
  const [slotName, setSlotName] = useState('');
  const [modalError, setModalError] = useState('');

  // 슬롯 상세 모달 (design.pen web-shelf-manage-slot)
  const [slotModalId, setSlotModalId] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      const [devs, cfgs, chems] = await Promise.all([
        getJSON<ShelfDevice[]>('/api/shelves'),
        getJSON<ShelfConfig[]>('/api/shelves/configs'),
        getJSON<Chemical[]>('/api/chemicals'),
      ]);
      setDevices(devs);
      // 생성 순서(id) 고정 — 서버/네트워크 순서가 흔들려도 카드 위치가 유지된다
      setConfigs([...cfgs].sort((a, b) => a.id.localeCompare(b.id)));
      setChemicals(chems);
    } catch {
      // 폴링 실패 무시
    } finally {
      setLoading(false);
    }
  };

  useWebSocket(() => {
    // WebSocket 실시간 메시지 수신 시 즉시 데이터 갱신
    fetchData();
  });

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 5000);
    return () => clearInterval(timer);
  }, []);

  const unregistered = devices.filter((d) => d.status === 'unregistered');

  // 폴링으로 데이터가 갱신되어도 모달이 최신 기기 정보를 보여주도록 id 로 조회
  const slotDevice = slotModalId ? devices.find((d) => d.id === slotModalId) ?? null : null;
  const slotChems = slotDevice
    ? chemicals.filter((c) => c.shelf_id === slotDevice.id && c.current_status === '비치중')
    : [];

  const addShelf = async () => {
    const used = new Set(configs.map((c) => c.id));
    let code = 'A'.charCodeAt(0);
    while (used.has(String.fromCharCode(code))) code += 1;
    const id = String.fromCharCode(code);
    try {
      // 1×1 최소 크기로 만들고, 바로 수정 모드로 열어 사용자가 행/열을 늘리게 한다
      await postJSON(`/api/shelves/configs/${id}`, { rows: 1, cols: 1 });
      setEditShelfId(id);
      await fetchData();
    } catch {
      // 무시
    }
  };

  const deleteShelf = async (config: ShelfConfig) => {
    const cnt = devices.filter(
      (d) => d.status === 'registered' && d.parent_shelf === config.id,
    ).length;
    const ok = window.confirm(
      `선반 ${config.id}을(를) 삭제할까요?${
        cnt > 0 ? `\n등록된 기기 ${cnt}대도 함께 등록 해제됩니다.` : ''
      }`,
    );
    if (!ok) return;
    try {
      await deleteJSON(`/api/shelves/configs/${config.id}`);
      setEditShelfId(null);
      await fetchData();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : '선반 삭제에 실패했습니다.');
    }
  };

  const openRegister = (t: RegisterTarget) => {
    if (unregistered.length === 0) return;
    setTarget(t);
    setDeviceId(unregistered[0].id);
    setSlotName(`${t.shelfId}${(t.row - 1) * (configs.find((c) => c.id === t.shelfId)?.cols ?? 1) + t.col}`);
    setModalError('');
  };

  const handleRegister = async (e: FormEvent) => {
    e.preventDefault();
    if (!target || !deviceId || !slotName) return;
    try {
      await postJSON(`/api/shelves/register/${deviceId}`, {
        name: slotName,
        parent_shelf: target.shelfId,
        row: target.row,
        col: target.col,
      });
      setTarget(null);
      await fetchData();
    } catch (err) {
      setModalError(err instanceof Error ? err.message : '기기 등록에 실패했습니다.');
    }
  };

  return (
    <div className="page">
      <div className="topbar">
        <div className="topbar-info">
          <h1 className="topbar-title" style={{ fontSize: 24 }}>선반 관리</h1>
          <span className="topbar-sub">
            Chemical Memory의 구역별 선반 상태 및 적재 현황을 실시간으로 모니터링합니다.
          </span>
        </div>
        {/* 새 선반 추가는 수정 모드(연필)에서만 노출 — 선반이 하나도 없을 때는 예외적으로 표시 */}
        {(editShelfId !== null || configs.length === 0) && (
          <Button pill icon={Plus} onClick={addShelf}>
            새 선반 추가
          </Button>
        )}
      </div>

      {/* 신규 등록 대기 기기 스트립 (design.pen Unreg Strip) */}
      {unregistered.length > 0 && (
        <div className="unreg-strip">
          <div className="unreg-strip-texts">
            <span className="unreg-strip-title">신규 등록 대기 기기 {unregistered.length}대</span>
            <span className="unreg-strip-sub">
              셀에서 등록 해제되거나 게이트웨이에 새로 접속된 기기입니다 — 빈 슬롯의 '여기에
              배치'를 눌러 등록하세요.
            </span>
          </div>
          <div className="unreg-strip-chips">
            {/* 방금 리셋 버튼이 눌린 기기는 맨 앞 + 블링크로 식별 */}
            {[...unregistered]
              .sort((a, b) => Number(b.recently_reset ?? false) - Number(a.recently_reset ?? false))
              .map((d) => (
                <span
                  key={d.id}
                  className={`unreg-chip${d.recently_reset ? ' unreg-chip-reset' : ''}`}
                >
                  <HardDrive size={18} />
                  {d.id}
                  {d.recently_reset ? (
                    <span className="unreg-chip-reset-tag">방금 리셋됨</span>
                  ) : (
                    <span className="unreg-chip-batt">{d.battery}%</span>
                  )}
                </span>
              ))}
          </div>
        </div>
      )}

      {loading ? (
        <div className="t-empty">데이터를 불러오는 중입니다...</div>
      ) : (
        <div className="shelves-area">
          {configs.map((config) => (
            <ShelfCard
              key={config.id}
              config={config}
              devices={devices}
              chemicals={chemicals}
              hasUnregistered={unregistered.length > 0}
              editing={editShelfId === config.id}
              selectedSlotId={slotDevice?.id ?? null}
              onToggleEdit={() =>
                setEditShelfId(editShelfId === config.id ? null : config.id)
              }
              onDeleteShelf={() => deleteShelf(config)}
              onOpenRegister={openRegister}
              onOpenSlot={(d) => setSlotModalId(d.id)}
              refresh={fetchData}
            />
          ))}
        </div>
      )}

      {/* 슬롯 상세 모달 — 슬롯 안 비치 시약 리스트 (design.pen web-shelf-manage-slot) */}
      {slotDevice && (
        <div className="modal-overlay" onClick={() => setSlotModalId(null)}>
          <div className="modal slot-modal" onClick={(e) => e.stopPropagation()}>
            <div className="slot-modal-head">
              <div className="slot-modal-title-row">
                <HardDrive size={20} />
                {slotDevice.name ?? slotDevice.id}
              </div>
              <span className="slot-modal-sub">
                선반 {slotDevice.parent_shelf} · {slotDevice.row}행 {slotDevice.col}열 ·{' '}
                {slotDevice.id} — 비치 시약 {slotChems.length}종
              </span>
            </div>

            <div className="slot-chem-list">
              {slotChems.length === 0 ? (
                <span className="slot-chem-empty">비치된 시약이 없습니다.</span>
              ) : (
                slotChems.map((c) => (
                  <div key={c.id} className="slot-chem-item">
                    <span className="slot-chem-item-icon">
                      <FlaskConical size={18} />
                    </span>
                    <div className="slot-chem-item-texts">
                      <span className="slot-chem-item-name">{c.name}</span>
                      <span className="slot-chem-item-meta">
                        {c.weight.toFixed(1)}kg · 유통기한 {c.expiration_date ?? '-'}
                      </span>
                    </div>
                    <Badge variant="success">비치중</Badge>
                  </div>
                ))
              )}
            </div>

            <button className="slot-modal-confirm" onClick={() => setSlotModalId(null)}>
              확인
            </button>
          </div>
        </div>
      )}

      {/* 미등록 기기 배치 모달 */}
      {target && (
        <div className="modal-overlay" onClick={() => setTarget(null)}>
          <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={handleRegister}>
            <h3 className="modal-title">
              기기 등록 — 선반 {target.shelfId} · {target.row}행 {target.col}열
            </h3>

            <div className="input-group">
              <span className="input-label">미등록 기기</span>
              <select
                className="input-field"
                value={deviceId}
                onChange={(e) => setDeviceId(e.target.value)}
              >
                {unregistered.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.id} (배터리 {d.battery}%)
                  </option>
                ))}
              </select>
            </div>

            <Input
              label="슬롯 이름"
              placeholder="예: A7"
              value={slotName}
              onChange={(e) => setSlotName(e.target.value)}
            />

            {modalError && <div className="form-error">{modalError}</div>}

            <div className="modal-actions">
              <Button type="button" variant="outline" onClick={() => setTarget(null)}>
                취소
              </Button>
              <Button type="submit">등록</Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
