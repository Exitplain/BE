"""받은 소스 코드(zip·git 스냅샷)를 읽기만 하는 정적 분석기. 코드를 실행하지 않는다.

- detect_framework: 프레임워크·라우터 감지 (모호하거나 미지원이면 추측하지 않고 blocked)
- extract_routes: 라우트·화면 이동 간선·퍼널 경로 (근거 file:line)
- static_site: 빌드 도구 없는 정적 사이트(HTML + JS) 분기

검증 스크립트는 ai_extra/skills/front_framework/verify_script 에 있다.
"""
