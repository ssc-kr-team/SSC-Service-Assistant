# 직접 파일관리형 제품 챗봇

변경 사항
- 모든 사용자가 자료 요청 시 다운로드 가능
- 파일 업로드 승인 과정 제거
- 업로드 즉시 반영
- 관리자는 업로드된 파일 삭제 가능

설정 순서
1. Supabase 프로젝트 생성
2. SQL Editor에서 schema.sql 실행
3. Storage에서 bucket `product-files` 생성
4. Render 환경변수 설정
5. 배포
