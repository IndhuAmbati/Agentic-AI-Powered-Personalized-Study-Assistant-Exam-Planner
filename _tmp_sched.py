import asyncio, json
from httpx import AsyncClient, ASGITransport
from backend.app.main import app
from backend.app.database import engine, Base

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        payload = {
            "user_id": "ec4c8d8e-6981-4ba0-b725-454e59a498c8",
            "start_date": "2026-03-12",
            "end_date": "2026-03-26",
            "daily_start_time": "08:00:00",
            "daily_study_hours": 4,
            "session_duration_mins": 60,
            "break_duration_mins": 15
        }
        r = await client.post('/api/schedule/generate', json=payload)
        print('status', r.status_code)
        print(r.text[:400])

asyncio.run(main())
