from sqlalchemy import Column, String

class AddressMixin:
    """공통 주소 정보"""
    c_address_line1 = Column(String, nullable=True)  # 기본 주소
    c_address_line2 = Column(String, nullable=True)  # 상세 주소
    c_city          = Column(String, nullable=True)  # 도시
    c_state         = Column(String, nullable=True)  # 주/도
    c_zip           = Column(String, nullable=True)  # 우편번호
    c_country       = Column(String, default="USA")  # 국가
