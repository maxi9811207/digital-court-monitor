# Digital Court 外部監看

在 GitHub 的機器上每 5 分鐘檢查 https://digital-court.cc（官網、預約系統 API、示範館），
跟正式站不在同一台主機，主機整台掛掉也叫得出來。

- 壞掉、恢復各通知一次（Email＋LINE），持續壞掉不會一直叫；HTTPS 憑證剩 14 天以內每週提醒一次。
- 檢查項目在 `monitor.py` 的 `CHECKS`；目前狀態在 `state.json`。
- 手動測試通知：Actions → Digital Court 監看 → Run workflow → 勾「只送一則測試通知」。
- 通知用的金鑰都放在 repo 的 Secrets，不在程式碼裡。
