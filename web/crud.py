from sqlalchemy.orm import Session
import models

def init_db_seed(db: Session):
    """최초 실행 시 로그인용 기본 사용자만 시드한다.

    선반·시약·반입반출 이력·주문 목록은 더미 없이 빈 상태로 시작한다.
    선반은 ESP 모듈이 접속하면 미등록 기기로 자동 생성되고(main.py),
    시약·이력은 앱의 반입/반출 스캔으로만 쌓인다.
    """
    if db.query(models.User).first() is not None:
        return

    # 웹 로그인 비밀번호는 qwer1234, 앱 로그인 PIN은 1234 로 통일한다.
    users = [
        models.User(username="kim.lab", password="qwer1234", pin="1234", nickname="김연구", role="관리자"),
        models.User(username="lee.exp", password="qwer1234", pin="1234", nickname="이실험", role="연구원"),
        models.User(username="park.safe", password="qwer1234", pin="1234", nickname="박안전", role="관리자"),
        models.User(username="jung.dev", password="qwer1234", pin="1234", nickname="정개발", role="연구원"),
        models.User(username="kang.design", password="qwer1234", pin="1234", nickname="강디자인", role="연구원"),
        models.User(username="yoon.plan", password="qwer1234", pin="1234", nickname="윤기획", role="연구원"),
        models.User(username="cho.test", password="qwer1234", pin="1234", nickname="조테스트", role="연구원"),
        models.User(username="shin.intern", password="qwer1234", pin="1234", nickname="신인턴", role="연구원"),
    ]
    for u in users:
        db.add(u)
    db.commit()
