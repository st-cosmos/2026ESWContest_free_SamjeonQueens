#!/bin/bash
# Thread 데이터셋 부팅 시 자동 프로비저닝 (thread-provision.service 가 실행).
#
# 이 빌드의 ot-daemon 은 데이터셋을 재부팅 시 잃어버리므로 (README §3 함정 참고)
# 매 부팅마다 고정 mesh-local prefix + 증가하는 타임스탬프(YYYYMMDDHH)로 다시
# 커밋한다. prefix 가 같고 타임스탬프가 항상 커지므로 노드들은 재커미셔닝 없이
# 수 초 내에 새 데이터셋을 전파받아 그대로 붙는다.
#
# 값은 ../firmware/prj.conf 의 크리덴셜과 정확히 일치해야 한다.
set -e

# ot-daemon 이 소켓을 열 때까지 대기 (부팅 직후 경쟁 방지)
for i in $(seq 1 30); do ot-ctl state >/dev/null 2>&1 && break; sleep 1; done

# ot-ctl 출력에는 CR 이 섞여 있어 그대로 비교하면 항상 어긋난다 - 제거 필수
state=$(ot-ctl state | head -1 | tr -d '\r\n ')
if [ "$state" != "disabled" ]; then
    echo "thread already up: $state"
    exit 0
fi

ot-ctl dataset init new
ot-ctl dataset channel 15
ot-ctl dataset panid 0xabcd
ot-ctl dataset networkname SmartShelf
ot-ctl dataset extpanid 1111111122222222
ot-ctl dataset networkkey 00112233445566778899aabbccddeeff
ot-ctl dataset meshlocalprefix fd59:ff6a:a426:1e98::
ot-ctl dataset activetimestamp "$(date +%Y%m%d%H)"
ot-ctl dataset commit active
ot-ctl ifconfig up
ot-ctl thread start
echo "thread provisioned, timestamp $(date +%Y%m%d%H)"
