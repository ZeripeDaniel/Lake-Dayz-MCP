# DayZ Enforce 검증 MCP — 서버 컨테이너.
#
# 컨테이너는 "서빙"만 한다. 데이터(data/dayz_scripts.db)는 호스트에서 만든다 —
# index_local.py가 P:\scripts(subst 가상 드라이브)를 읽어야 해서 컨테이너에선 못 만든다.
# 그래서 호스트에서 빌드한 DB + 모드셋 소스 + 가이드를 read-only로 마운트해서 띄운다.
#
# 1) 이미지 빌드:
#      docker build -t lake-dayz .
#
# 2) Claude Code에 stdio MCP로 등록 (한 줄):
#      claude mcp add -s user lake-dayz -- ^
#        docker run -i --rm ^
#          -v "<repo>\enforce-mcp\data:/data:ro" ^
#          -v "<your-mod-source-root>:/modset:ro" ^
#          -v "<dir-containing-enforce-script-guide.md>:/docs:ro" ^
#          lake-dayz
#
# 게임 업데이트 후 데이터 갱신은 호스트에서 (README "데이터 갱신" 참고). 마운트가 read-only라
# 호스트에서 DB만 다시 만들면 컨테이너가 자동으로 새 DB를 본다 (이미지 재빌드 불필요).
FROM python:3.12-slim

WORKDIR /app

# 서버 런타임 의존성만 (crawl/index는 호스트에서 도므로 requests/bs4/lxml 불필요)
RUN pip install --no-cache-dir "mcp>=1.2.0"

COPY server.py .

# 마운트 지점 (docker run -v 로 호스트 경로 연결)
ENV DAYZ_MCP_DB=/data/dayz_scripts.db \
    DAYZ_MCP_MODSET=/modset \
    DAYZ_MCP_GUIDE=/docs/enforce-script-guide.md

# stdio MCP: stdin/stdout 이 통신 채널. docker run -i 필수.
CMD ["python", "server.py"]
