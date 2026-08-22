# LLM 交接模式

這個模式用來避免在本機網頁服務內設定 `OPENAI_API_KEY`。

限制很清楚：本機 FastAPI 服務沒有辦法直接使用你在某個 LLM 桌面 App／網頁對話裡的額度。那份額度只能在該對話中使用——由它讀取檔案、分析、把結果寫回來。

流程：

1. 在網頁輸入 YouTube URL。
2. 本機服務下載影片、抓字幕或轉錄、切出初步片段。
3. 若沒有 `OPENAI_API_KEY`，job folder 會產生 `llm_handoff.md`。
4. 回到你的 LLM 對話，請它處理該 job。
5. 它讀取逐字稿，使用該對話的額度產出 `summary.md`、`analysis.md`、`segments.md`。

這樣可以省掉 API key，但它不是全自動手機 App 流程。若要手機 App 按一次就自動完成翻譯、摘要和深度分析，後端仍需要一個可被程式呼叫的模型來源：OpenAI API key、本機 LLM，或其他模型服務。
