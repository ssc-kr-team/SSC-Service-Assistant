# 관리자 UI 포함 다중 파일 연결형 제품 챗봇

포함 기능
- 관리자 탭 UI
  - 제품 리스트 보기
  - 파일 리스트 보기
  - 제품 파일 업로드
  - PDF 자동추출
  - 후보 승인
  - 파일 삭제
- 고객/직원 챗봇
  - 검색 / 비교 / 추천
  - 사양서 / 신뢰성 / 인증서 / LM80 / TM21 다운로드 링크 제공

설정 순서
1. Supabase 프로젝트 생성
2. SQL Editor에서 schema.sql 실행
3. Storage에서 bucket `product-files` 생성
4. Render 환경변수 설정
5. 배포

관리자 토큰
- korea_lighting_2026
