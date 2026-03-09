from sqlalchemy import Column, String

class ContactMixin:
    """공통 연락처 정보"""
    c_first_name = Column(String, nullable=True)  # 이름
    c_last_name  = Column(String, nullable=True)  # 성
    c_email      = Column(String, nullable=True)  # 이메일
    c_phone      = Column(String, nullable=True)  # 전화번호
