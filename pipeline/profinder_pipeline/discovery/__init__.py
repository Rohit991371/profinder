"""Discovery stage (S2, task 2.5): find faculty-list, admissions and scholarship pages per university.

Order: manual seed URLs -> bounded polite link crawl from the official site -> Tavily/Serper search fallback.
Only allow-listed hosts are ever fetched. A miss is recorded as `no_page_found`; nothing is guessed.
Run with: python -m profinder_pipeline discover --help
"""
