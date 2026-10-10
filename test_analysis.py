#!/usr/bin/env python
from secmon.database import SessionLocal
from secmon.models import Filing
from secmon.services.analysis import GeminiAnalysisService
from secmon.config import get_settings
from sqlalchemy import select

settings = get_settings()
service = GeminiAnalysisService(settings)

with SessionLocal() as db:
    # Find a T1 filing with text
    filing = db.execute(select(Filing).where(
        Filing.tier == 1,
        Filing.raw_document_text != None,
        Filing.analysis == None
    ).limit(1)).scalar_one_or_none()
    
    if not filing:
        print('No eligible filing found')
    else:
        print(f'Testing: {filing.ticker} {filing.form_type}')
        print(f'Text length: {len(filing.raw_document_text)}')
        try:
            result = service.analyze(
                ticker=filing.ticker,
                company_name=filing.company_name,
                form_type=filing.form_type,
                document_text=filing.raw_document_text
            )
            print(f'Analysis success: {bool(result)}')
            if result:
                summary = result.summary[:200] if result.summary else "None"
                print(f'Summary: {summary}')
        except Exception as e:
            print(f'Error: {type(e).__name__}: {e}')
