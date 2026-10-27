import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Check, ExternalLink, History, Minus, Plus, ShoppingCart, X } from 'lucide-react';
import Badge from '../components/Badge';
import Button from '../components/Button';
import Input from '../components/Input';
import ProgressBar from '../components/ProgressBar';
import { getJSON, postJSON, putJSON } from '../api';
import type { Order } from '../types';

// 테이블 컬럼 폭 (design-spec §5.2)
const COL = {
  check: 52,
  remain: 170,
  threshold: 100,
  qty: 150,
  price: 130,
  link: 116,
};

function parsePct(qty: string | null): number {
  const n = parseInt(qty ?? '', 10);
  return Number.isNaN(n) ? 0 : n;
}

function won(amount: number): string {
  return `₩${amount.toLocaleString('ko-KR')}`;
}

export default function Orders() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [qty, setQty] = useState<Record<number, number>>({});
  const [showHistory, setShowHistory] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  // 구매 링크 메모 모달
  const [linkTarget, setLinkTarget] = useState<Order | null>(null);
  const [linkValue, setLinkValue] = useState('');
  const [linkError, setLinkError] = useState('');

  const fetchOrders = async () => {
    try {
      setOrders(await getJSON<Order[]>('/api/orders'));
    } catch {
      // 무시
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOrders();
  }, []);

  const qtyOf = (id: number) => qty[id] ?? 1;
  const changeQty = (id: number, delta: number) =>
    setQty((prev) => ({ ...prev, [id]: Math.max(1, qtyOf(id) + delta) }));

  const pending = orders.filter((o) => o.status === 'pending');
  const ordered = orders.filter((o) => o.status === 'ordered');
  const list = showHistory ? ordered : pending;
  const selectedOrders = pending.filter((o) => o.selected);
  const totalAmount = selectedOrders.reduce((sum, o) => sum + o.price * qtyOf(o.id), 0);

  const flash = (ok: boolean, text: string) => {
    setNotice({ ok, text });
    setTimeout(() => setNotice(null), 4000);
  };

  const isAllSelected = pending.length > 0 && pending.every((o) => o.selected);

  const toggleSelectAll = async () => {
    if (showHistory || pending.length === 0) return;
    const nextState = !isAllSelected;

    setOrders((prev) =>
      prev.map((o) => (o.status === 'pending' ? { ...o, selected: nextState } : o)),
    );

    try {
      await Promise.all(
        pending.map((o) => putJSON(`/api/orders/${o.id}`, { selected: nextState })),
      );
    } catch (err) {
      await fetchOrders();
      flash(false, err instanceof Error ? err.message : '선택 변경에 실패했습니다.');
    }
  };

  const toggleSelect = async (order: Order) => {
    const next = !order.selected;
    setOrders((prev) => prev.map((o) => (o.id === order.id ? { ...o, selected: next } : o)));
    try {
      await putJSON(`/api/orders/${order.id}`, { selected: next });
    } catch {
      // 실패 시 롤백
      setOrders((prev) =>
        prev.map((o) => (o.id === order.id ? { ...o, selected: order.selected } : o)),
      );
    }
  };

  const excludeSelected = async () => {
    try {
      await Promise.all(
        selectedOrders.map((o) => putJSON(`/api/orders/${o.id}`, { selected: false })),
      );
      await fetchOrders();
    } catch (err) {
      flash(false, err instanceof Error ? err.message : '후보 제외에 실패했습니다.');
    }
  };

  const openLinkModal = (order: Order) => {
    setLinkTarget(order);
    setLinkValue(order.purchase_link ?? '');
    setLinkError('');
  };

  const saveLink = async (e: FormEvent) => {
    e.preventDefault();
    if (!linkTarget) return;
    try {
      await putJSON(`/api/orders/${linkTarget.id}`, { purchase_link: linkValue.trim() });
      setLinkTarget(null);
      await fetchOrders();
    } catch (err) {
      setLinkError(err instanceof Error ? err.message : '링크 저장에 실패했습니다.');
    }
  };

  const confirmOrders = async () => {
    try {
      const res = await postJSON<{ status: string; count: number }>('/api/orders/confirm');
      flash(true, `${res.count}건의 주문이 확정되었습니다.`);
      await fetchOrders();
    } catch (err) {
      flash(false, err instanceof Error ? err.message : '주문 컨펌에 실패했습니다.');
    }
  };

  return (
    <div className="page">
      <div className="topbar">
        <div className="topbar-info">
          <h1 className="topbar-title">주문 관리</h1>
          <span className="topbar-sub">
            {showHistory
              ? `주문 완료된 시약 ${ordered.length}건`
              : `재고 부족으로 주문 후보에 등록된 시약 ${pending.length}건`}
          </span>
        </div>
        <Button variant="outline" icon={History} onClick={() => setShowHistory(!showHistory)}>
          {showHistory ? '주문 후보' : '주문 내역'}
        </Button>
      </div>

      <div className="card table-card">
        <div className="t-head">
          <div className="t-cell" style={{ width: COL.check }}>
            {!showHistory && (
              <button
                type="button"
                className={`checkbox${isAllSelected ? ' checked' : ''}`}
                onClick={toggleSelectAll}
                title={isAllSelected ? '전체 선택 해제' : '전체 선택'}
              >
                {isAllSelected && <Check size={11} />}
              </button>
            )}
          </div>
          <div className="t-cell grow">시약 이름</div>
          <div className="t-cell" style={{ width: COL.remain }}>현재 잔량</div>
          <div className="t-cell" style={{ width: COL.threshold }}>기준 수량</div>
          <div className="t-cell" style={{ width: COL.qty }}>주문 수량</div>
          <div className="t-cell" style={{ width: COL.price }}>예상 금액</div>
          <div className="t-cell" style={{ width: COL.link }}>구매 링크</div>
        </div>
        <div className="t-body">
          {loading ? (
            <div className="t-empty">데이터를 불러오는 중입니다...</div>
          ) : list.length === 0 ? (
            <div className="t-empty">
              {showHistory ? '주문 내역이 없습니다.' : '주문 후보가 없습니다.'}
            </div>
          ) : (
            list.map((order) => {
              const checked = !showHistory && order.selected;
              return (
                <div key={order.id} className={`t-row${checked ? ' checked' : ''}`}>
                  <div className="t-cell" style={{ width: COL.check, padding: '14px 16px' }}>
                    {showHistory ? (
                      <Badge variant="primary">완료</Badge>
                    ) : (
                      <button
                        className={`checkbox${order.selected ? ' checked' : ''}`}
                        onClick={() => toggleSelect(order)}
                        title={order.selected ? '선택 해제' : '선택'}
                      >
                        {order.selected && <Check size={11} />}
                      </button>
                    )}
                  </div>
                  <div className="t-cell grow" style={{ padding: '12px 16px' }}>
                    <div className="cell-title">
                      {order.purchase_link ? (
                        <a
                          className="cell-title-main cell-title-link"
                          href={order.purchase_link}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="구매 사이트로 이동"
                        >
                          {order.chemical_name}
                          <ExternalLink size={13} />
                        </a>
                      ) : (
                        <span className="cell-title-main">{order.chemical_name}</span>
                      )}
                      <span className="cell-title-sub">
                        {[order.formula, order.manufacturer].filter(Boolean).join(' · ')}
                      </span>
                    </div>
                  </div>
                  <div className="t-cell" style={{ width: COL.remain, padding: '12px 16px' }}>
                    <ProgressBar percent={parsePct(order.current_qty)} trackWidth={64} danger />
                  </div>
                  <div className="t-cell" style={{ width: COL.threshold, padding: '12px 16px' }}>
                    {order.threshold_qty ?? '-'}
                  </div>
                  <div className="t-cell" style={{ width: COL.qty, padding: '12px 16px' }}>
                    {showHistory ? (
                      <span>{qtyOf(order.id)}</span>
                    ) : (
                      <div className="stepper">
                        <button className="stepper-btn" onClick={() => changeQty(order.id, -1)}>
                          <Minus size={13} />
                        </button>
                        <span className="stepper-count">{qtyOf(order.id)}</span>
                        <button className="stepper-btn plus" onClick={() => changeQty(order.id, 1)}>
                          <Plus size={13} />
                        </button>
                      </div>
                    )}
                  </div>
                  <div
                    className="t-cell"
                    style={{
                      width: COL.price,
                      padding: '12px 16px',
                      fontWeight: 600,
                      color: 'var(--text-strong)',
                    }}
                  >
                    {won(order.price * qtyOf(order.id))}
                  </div>
                  <div className="t-cell" style={{ width: COL.link, padding: '12px 16px' }}>
                    {order.purchase_link ? (
                      <button
                        className="link-chip"
                        onClick={() => openLinkModal(order)}
                        title={`링크 수정 — ${order.purchase_link}`}
                      >
                        <ExternalLink size={13} />
                        링크
                      </button>
                    ) : (
                      <button
                        className="link-add-chip"
                        onClick={() => openLinkModal(order)}
                        title="구매 링크 추가"
                      >
                        <Plus size={12} />
                        링크 추가
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {notice && <div className={notice.ok ? 'form-success' : 'form-error'}>{notice.text}</div>}

      {!showHistory && (
        <div className="footer-bar">
          <div className="footer-bar-summary">
            <ShoppingCart size={18} />
            {selectedOrders.length}건 선택 · 예상 합계 {won(totalAmount)}
          </div>
          <div className="footer-bar-actions">
            <Button
              variant="outline"
              icon={X}
              onClick={excludeSelected}
              disabled={selectedOrders.length === 0}
            >
              후보에서 제외
            </Button>
            <Button icon={Check} onClick={confirmOrders} disabled={selectedOrders.length === 0}>
              선택 항목 주문 컨펌
            </Button>
          </div>
        </div>
      )}

      {/* 구매 링크 메모 모달 */}
      {linkTarget && (
        <div className="modal-overlay" onClick={() => setLinkTarget(null)}>
          <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={saveLink}>
            <h3 className="modal-title">구매 링크 메모</h3>
            <span className="modal-sub">
              {[linkTarget.chemical_name, linkTarget.formula, linkTarget.manufacturer]
                .filter(Boolean)
                .join(' · ')}
            </span>

            <Input
              label="구매 사이트 URL"
              placeholder="https://example.com/product/..."
              value={linkValue}
              onChange={(e) => setLinkValue(e.target.value)}
            />

            <span className="modal-help">
              저장하면 주문 목록에서 시약 이름을 클릭할 때 이 링크로 이동합니다.
            </span>

            {linkError && <div className="form-error">{linkError}</div>}

            <div className="modal-actions">
              <Button type="button" variant="outline" onClick={() => setLinkTarget(null)}>
                취소
              </Button>
              <Button type="submit" icon={Check}>저장</Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
