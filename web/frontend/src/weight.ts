/**
 * 화면 표시용 무게(kg). 로드셀 영점 오차로 빈 칸이 -0.05kg 처럼 음수로 측정돼도 0 으로 보인다.
 * 서버의 반입·반출 판정은 실측값을 그대로 쓰므로, 저장값이 아니라 표시할 때만 자른다.
 */
export function displayKg(kg: number): number {
  return Math.max(0, kg);
}

/** 화면에 쓰는 무게 문자열. 앱(WeightDisplay.kt formatKg)과 같게 소수점 둘째 자리까지 고정 ("0.50"). */
export function formatKg(kg: number): string {
  return displayKg(kg).toFixed(2);
}
