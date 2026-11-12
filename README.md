# Unity 劍龍森林

在 Unity 6.3 LTS 裡組裝有骨架的劍龍與森林場景，並用五個鏡頭拍攝走路、奔跑、甩頭、抬頭吼叫姿勢和環繞。劍龍的可編輯零件共用同一副骨架，環境物件可逐個調整。

- [Unity 操作與驗證紀錄](docs/操作紀錄.md)
- [素材來源與授權](docs/素材來源.md)

目前素材、場景建構器與渲染腳本已備妥。實際 Unity 匯入與渲染的狀態見[操作紀錄](docs/操作紀錄.md)。

## 開啟專案

1. 安裝 [Unity 6000.3.28f1](https://unity.com/releases/editor/whats-new/6000.3.28f1)，並在 [Unity Hub 啟用可用授權](https://docs.unity.com/hub/manage-license)。
2. 在 Unity Hub 選 **Projects → Add → Add project from disk**，指向含有 `Assets`、`Packages`、`ProjectSettings` 的 repo 根目錄。等 Unity 完成匯入和腳本編譯。
3. 在 Editor 選 **Dinosaug → Build Forest Scene**。開啟 `Assets/Scenes/StegosaurForest.unity` 後，可以在 Hierarchy 調整劍龍、森林物件和燈光。劍龍零件的網格資產在 `Assets/Generated/DinosaurParts/`，可重用的模型 Prefab 在 `Assets/Generated/Prefabs/`。
4. 選 **Dinosaug → Render Forest Smoke Test** 先輸出少量預覽畫格，確認構圖後，再選 **Dinosaug → Render Forest Film** 輸出完整 PNG 序列。若場景已在 Editor 開啟，選單會取用目前畫面中的修改。批次渲染讀取已儲存的場景。

## 分件設計

- 劍龍的頭頸、軀幹、四隻腳、尾巴、背板和尾刺是獨立的蒙皮網格。組裝時共用骨架與材質，四段動作的綁定路徑保留在同一個角色階層。
- 樹、松樹苗、樹樁、五種灌木、落葉地面、天空、燈光和攝影機各自建立。主路徑留給角色移動與低機位跟拍，灌木分布在路緣和樹下。
- 走路與奔跑由骨架動作和場景位移共同呈現。環繞是攝影機運動。抬頭吼叫姿勢沿用原有骨架，缺少下顎動畫和吼叫音軌。

## 批次渲染

Windows 可使用 `Tools/Render-UnityForest.ps1` 指定 Unity Editor。需要 MP4 時，請讓腳本能找到 FFmpeg 和 FFprobe。完整指令與驗證狀態見[操作紀錄](docs/操作紀錄.md)。
