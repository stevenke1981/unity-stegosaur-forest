# 劍龍森林九鏡頭製作流程

重建前腳修正版的 Blender 角色、森林場景、Unity HDRP 與 Three.js 影格，並以 FFmpeg 合成及驗證兩版 MP4。Blender、FBX、GLB 和森林素材沿用原交付包。這份倉庫提供可執行腳本、場景清單、鏡頭時序、材質程式和檢查項目。

## 1. 準備環境與素材

| 工具 | 用途 |
| --- | --- |
| Windows、已啟用授權的 Unity 6000.3.28f1、Direct3D 11 | Unity 渲染。使用 Editor 隨附的 HDRP 17.3.0。 |
| Python 3.11+、Node.js 20+ 和 npm | 執行流程與安裝已鎖定的 Three.js 0.183.2、Playwright Core 1.64.1。 |
| Chrome | Three.js 渲染與成片播放檢查，使用本機的獨立無頭瀏覽器。 |
| FFmpeg 和 FFprobe，含 `libx264` | 合成、逐格查驗時間戳和剪接點、驗證音軌與全片解碼。 |
| Blender 4.5.3 LTS | 重拓樸、修改蒙皮和表情、烘焙貼圖與匯出角色。使用交付包中已匯出的模型時可直接渲染。 |

從原交付資料夾取得 `Stegosaur-ForelegFix-Project.zip`、`Stegosaur-ForelegFix-Unity-Forest.zip`、`Stegosaur-ForelegFix-ThreeJS-Forest.zip`。把專案 ZIP 和所需引擎的森林 ZIP 解壓到同一個新目錄，該目錄就是 `--assets`。首次執行時，npm 與 Unity Package Manager 可能需要下載相依套件。Three.js 擷取期間所有網頁資源從本機迴路讀取。 [素材來源與授權](../../docs/素材來源.md)

| 輸入相對路徑 | 內容 |
| --- | --- |
| `Assets/Source/Dinosaur/CreatureStegosaur.fbx` | Unity 角色及烘焙動作 |
| `Assets/Source/Environment/`、`Assets/Source/Textures/` | Unity 森林、地形、HDRI 與角色材質，檔名依[場景清單](project/Assets/Source/asset-manifest.json) |
| `Web/assets/stegosaur-quad30k.glb` | Three.js 角色、動作和基礎材質 |
| `Web/assets/` | Three.js 森林、HDRI 和貼圖，檔名依 [`WEB_ASSETS`](workflow.py) |
| `Web/assets/roar.m4a` | 與剪接對齊的 48 kHz 單聲道 AAC，可用 `--audio` 指定同規格的成片音軌 |

## 2. 執行雙引擎流程

在 GitHub 倉庫根目錄開啟 PowerShell。請替換工具和素材路徑。每個 `--output` 都使用一個尚未建立的目錄，工作副本和結果會寫入該目錄。

```powershell
$flow = 'workflows/stegosaur-nine-shots'
$assets = 'C:/Media/Stegosaur-ForelegFix'
$out = 'C:/Media/Stegosaur-Render'
$unity = 'C:/Program Files/Unity 6000.3.28f1/Editor/Unity.exe'
$chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe'

python "$flow/workflow.py" check --assets $assets --unity $unity --chrome $chrome
python "$flow/workflow.py" smoke --assets $assets --unity $unity --chrome $chrome --output "$out/smoke"
python "$flow/workflow.py" render --assets $assets --unity $unity --chrome $chrome --output "$out/full"
```

- `check` 確認素材檔名、引擎入口和本機 HDRP 版本。`smoke` 各輸出奔跑和吼叫的 18 個診斷影格。兩個模式適合在換模型或材質時先跑。
- `render` 為各引擎重建場景、渲染 676 格並合成 MP4。`smoke` 和 `render` 可用 `--engine unity` 或 `--engine three` 單獨執行。`render --engine unity` 也會使用 Chrome 檢查成片播放。
- `project/` 是可繼續編輯的工作副本。可在 Unity Hub 開啟該根目錄，再開啟 `Assets/Scenes/StegosaurCreatureForest.unity`。要打開 Three.js 播放器，可在渲染工作副本執行 `node "$out/full/project/Web/server.mjs"`，再開啟終端機顯示的本機網址。
- 捕捉紀錄在 `unity-capture/` 和 `three-capture/`。成片、兩引擎的影音與播放檢查、雜湊和 `workflow-report.json` 位於輸出根目錄。主報告在所有選定階段通過後才寫入。

已有完整影格時可單獨重合成。保留各引擎擷取資料夾內的原報告和 `frames/`，依需求指定一個或兩個擷取資料夾。

```powershell
python "$flow/workflow.py" conform --chrome $chrome --audio "$assets/Web/assets/roar.m4a" `
  --unity-capture "$out/full/unity-capture" --three-capture "$out/full/three-capture" `
  --output "$out/re-encode"
```

## 3. Blender 重建角色

### 重拓樸和分件

原始角色以 Meshy 生成模型和基礎骨架，再在 Blender 加入獨立下顎、四足 IK、反向腳掌支點、後腳飛節、胸頸和尾骨控制。[骨架建立腳本](blender/build_creature_rig.py) 的輸入是已補下顎的劍龍 Blend。以下從已完成這些控制的 `Stegosaur-Creature-Source.blend` 開始，原九鏡頭的 `Stegosaur-Quad30K-Rebuild-and-Evidence.zip` 保留此檔。

用原表面先做約 8,900 三角面的導引網格，將閉嘴時相鄰的上下唇依下顎權重分開。肩、肘、前踝、髖、膝、跗關節、後踝、頸及尾部共 18 處，每處切出三圈互不交叉的閉合環線。以拓樸細分轉成四角面，再補環線達主體 29,736 面，加上舌頭的 264 面。

用最鄰近且朝向相符的原三角面，以重心座標轉移 UV、權重、形變，並保留來源 UV 島及上下唇分離。完整表面計算後按部位切為 25 個可編輯零件，共用 36 根變形骨和 35 根控制骨。`JointRing` 記錄網格邊所屬環線，`CageVertex` 讓接縫共用頂點可對齊位置、法線、形變與權重。骨架保留雙節 IK、膝向控制、腳掌著地鎖點和獨立的下顎。原場景中的大樹、松樹苗、樹樁、灌木和地面也可分開編輯。

```powershell
$blender = 'C:/Program Files/Blender Foundation/Blender 4.5/blender.exe'
$work = 'C:/Media/Stegosaur-Blender-Work'
$original = 'C:/Media/Stegosaur-Baseline/Stegosaur-Creature-Source.blend'
New-Item -ItemType Directory -Path $work

& $blender -b -t 8 --python-exit-code 1 --python "$flow/blender/detail_quad_seed.py" -- `
  --source $original --output-dir $work --targets 8900 --stem lip-seed
& $blender -b "$work/lip-seed-8900.blend" -t 8 --python-exit-code 1 --python "$flow/blender/retopologize_stegosaur.py" -- `
  --output "$work/Stegosaur-Quad30K-Base.blend" --report "$work/quad30k-build.json"
```

### 修正轉向、臉部和前腳

1. 左右轉各 84 格。步態烘焙減少骨盆下沉、前腳落地前探和趾跟滾動，降低後腿拉伸。角色在場景中的根位移和轉向由[共用時序檔](project/Assets/Source/Dinosaur/CreatureMotion.json)定義。後續微調後腿蒙皮和吼叫臉頰／喉嚨形變，再將原表面投影到連續的皮膚 UV，減弱頸側到前肩的色界。
2. 沿嘴角輪廓平滑局部凹痕，保持口腔與臉皮的接邊，加入視線及眨眼。吼叫用 `LowerJaw`、喉部、嘴角和鼻孔形變配合頭頸動作。
3. 前腳在移動時的放射狀尖片來自關節附近不連續的蒙皮變形。用 `CageVertex` 在接縫兩側算同一組權重，從胸肩、前肘、前踝到腳趾分配連續權重，對內側腿到胸腹的過渡做 12 次平滑。每點保留至多八項權重，正規化後寫回各分件。
4. 每個動作以 24 fps 逐格烘焙。輸出 FBX 和 GLB 時仍保留分件和變形骨，渲染網格三角化為 60,000 面。GLB 的唇部因材質分割，顯示為 26 個蒙皮圖元。烘焙的七種形變為 `Blink`、`Breath`、`EyeTrack`、`NostrilFlare`、`RoarBlink`、`RoarTension`、`Throat`。

從重拓樸後的 Blend 重建轉向和表情時，原九鏡頭重建包內有 `Source/CreatureMotion-Original.json`。原表面投影檔取自 `Stegosaur-Refinement-Engine-and-Evidence.zip` 的 `Rebuild/Stegosaur-Original-Projection-Surface.blend`。最後一個腳本會更新指定的動作 JSON，先複製到工作目錄再執行。

```powershell
$originalMotion = 'C:/Media/Stegosaur-Baseline/CreatureMotion-Original.json'
$surface = 'C:/Media/Stegosaur-Baseline/Stegosaur-Original-Projection-Surface.blend'

& $blender -b "$work/Stegosaur-Quad30K-Base.blend" --python-exit-code 1 --python "$flow/blender/retune_stegosaur_turns.py" -- `
  --motion $originalMotion --new-motion "$work/CreatureMotion-Turn.json" --output "$work/Stegosaur-Quad30K-Turn.blend"
& $blender -b "$work/Stegosaur-Quad30K-Turn.blend" --python-exit-code 1 --python "$flow/blender/refine_stegosaur.py" -- `
  --output "$work/Stegosaur-Quad30K-Skin.blend" --report "$work/skin-refine.json"
& $blender -b "$work/Stegosaur-Quad30K-Skin.blend" --python-exit-code 1 --python "$flow/blender/refine_stegosaur_atlas.py" -- `
  --original-surface $surface --output "$work/Stegosaur-Quad30K-Refined.blend" --maps "$work/atlas" --report "$work/atlas.json"
Copy-Item -LiteralPath "$work/CreatureMotion-Turn.json" -Destination "$work/CreatureMotion-Roar.json"
& $blender -b "$work/Stegosaur-Quad30K-Refined.blend" --python-exit-code 1 --python "$flow/blender/refine_roar_expression.py" -- `
  --motion "$work/CreatureMotion-Roar.json" --web-motion "$work/motion.json" `
  --output "$work/Stegosaur-Quad30K-RoarExpression.blend" --report "$work/roar.json"
```

也可從吼叫表情交付包裡的 `Stegosaur-Quad30K-RoarExpression.blend` 直接重跑前腳修正。最新版專案 ZIP 提供 `Stegosaur-Quad30K-Forelegs.blend` 及 `Evidence/quad30k-build-c.json`。若使用最新版 Blend，從拓樸檢查或匯出開始。環線標籤可用舊報告或上一節新產生的報告核對。每次 Blender 指令應確認結束碼為 0，再使用產出的檔案繼續。

```powershell
$previous = 'C:/Media/Stegosaur-RoarExpression/Stegosaur-Quad30K-RoarExpression.blend'
$rings = "$assets/Evidence/quad30k-build-c.json"
$motion = "$flow/project/Assets/Source/Dinosaur/CreatureMotion.json"
$corrected = "$work/Stegosaur-Quad30K-Forelegs.blend"
# 若使用剛重建的表情版本，改用工作目錄內的輸出。
# $previous = "$work/Stegosaur-Quad30K-RoarExpression.blend"
# $rings = "$work/quad30k-build.json"
# $motion = "$work/CreatureMotion-Roar.json"

& $blender -b $previous --python-exit-code 1 --python "$flow/blender/fix_front_left_leg.py" -- `
  --output $corrected --report "$work/binding.json"
& $blender -b $corrected --python-exit-code 1 --python "$flow/blender/check_quad_stegosaur.py" -- `
  --build $rings --output "$work/topology.json"
foreach ($part in 'FrontLeftLeg','FrontRightLeg') {
  & $blender -b $previous --python-exit-code 1 --python "$flow/blender/measure_front_left_leg.py" -- `
    --motion $motion --part $part --output "$work/$part-before.json"
  & $blender -b $corrected --python-exit-code 1 --python "$flow/blender/measure_front_left_leg.py" -- `
    --motion $motion --part $part --output "$work/$part-after.json"
}
& $blender -b $corrected --python-exit-code 1 --python "$flow/blender/export_quad_stegosaur.py" -- `
  --motion $motion --fbx "$work/CreatureStegosaur.fbx" --glb "$work/stegosaur-quad30k.glb"
& $blender -b $corrected --python-exit-code 1 --python "$flow/blender/build_skin_detail.py" -- `
  --output "$work/textures" --report "$work/skin.json"
```

套用重匯版本時，在素材副本中把 FBX 放入 `Assets/Source/Dinosaur/`、GLB 放入 `Web/assets/`。若重做皮膚 UV，將新圖集的 `Albedo.png`、`Normal.png` 和 `MetallicGloss.png` 複製到素材副本的 `Assets/Source/Textures/BakedMaterial/`。將兩張皮膚細節貼圖放到 `Assets/Source/Textures/StegosaurSkin/` 和 `Web/assets/`。若另改了動作資料，將對應 JSON 套到範本內的共用時序檔，再執行雙引擎流程。

## 4. 兩個引擎的場景、材質與時序

| 項目 | 實作 |
| --- | --- |
| 共用時序 | [`CreatureMotion.json`](project/Assets/Source/Dinosaur/CreatureMotion.json) 統一動作取樣、鏡頭長度、角色在世界座標的位移及轉向。建置時複製為 Three.js 的 `motion.json`。 |
| 角色與地面 | Blender 保留腳掌和腳跟支點。Unity 在烘焙動作上取樣地形高度，對四隻腳補償小起伏。Three.js 使用共用地面軌跡及已烘焙的腳步。 |
| 尾巴拖尾 | 兩個引擎對五段尾骨執行阻尼彈簧，固定步長 1/120 秒，每個渲染格五步，切鏡時重設。限制偏轉角與骨段長度。 |
| Unity HDRP | Deferred Lit、Linear、ACES、HDRI、方向光與原生 SSS，場景 Volume 指派 `ReptileSkin` Diffusion Profile，攝影機啟用 SSS。使用本機 Unity 的 HDRP 套件產生著色器；GitHub 存放產生器和自訂的皮膚 HLSL。 |
| Three.js | WebGL 2、`MeshPhysicalMaterial`、HDRI 經 PMREM 作環境照明、PCF 陰影與 ACES。以包裹漫反射及厚度遮罩的逆光透射近似皮膚散射。 |
| 兩組蒙皮權重 | Unity 用 FBX 的八權重蒙皮。Three.js 將 `JOINTS_1`／`WEIGHTS_1` 加到頂點、法線和陰影路徑。流程在輸出副本調整鎖定版本的 `GLTFLoader`，有第二組權重時保留 GLB 的原值，避免只將前四項正規化。 |
| 皮膚貼圖 | sRGB 基礎色、線性宏觀法線、512² 線性 `SkinDetail.png` 存微法線 XY 和縫隙，48×24 平鋪。2048×1024 線性 `SkinRegions.png` 存濕潤、薄皮與皺紋遮罩。微細節與宏觀法線疊加，縫隙控制粗糙度變化。下顎角度驅動嘴部及喉嚨皺紋，濕亮由遮罩限制在局部。 |
| 成片 | FFmpeg 以 H.264 720p／24 fps 輸出，九個剪接點強制關鍵格並直接複製 AAC。查驗 676 格、每格時間戳、音軌雜湊、全片解碼與 Chrome 播放。 |

引擎與匯出器參考。[Unity 6000.3 HDRP](https://docs.unity.com/en-us/engine/6000.3/manual/packages-list/packages-all/pack-core/com.unity.render-pipelines.high-definition)、[Three.js r183](https://github.com/mrdoob/three.js/releases/tag/r183)、[Blender glTF 匯出](https://docs.blender.org/manual/en/4.5/addons/import_export/scene_gltf2.html)

| 鏡頭 ID | 畫面 | 首格（從 0 算） | 格數 |
| --- | --- | ---: | ---: |
| `idle` | 待機廣角 | 0 | 66 |
| `walk` | 行走跟拍 | 66 | 102 |
| `run` | 奔跑低機位 | 168 | 75 |
| `left` | 左轉高機位 | 243 | 84 |
| `right` | 右轉反向鏡位 | 327 | 84 |
| `roar` | 吼叫特寫 | 411 | 78 |
| `bite` | 試咬頭部鏡位 | 489 | 58 |
| `tail` | 甩尾跟拍 | 547 | 67 |
| `reaction` | 受擊側面 | 614 | 62 |

## 5. 檢查重點與移植方式

- 奔跑在第 206、211、216、222、229 格檢查兩隻前腳。9.000 秒對應全片第 216 格和奔跑鏡頭第 48 格。量測以靜止姿勢為基準，檢查三角面積倍率及前踝以下的最大邊長增量。吼叫在第 456 格（19.000 秒）檢查最大開口、眼睛與喉部。
- Unity 輸出同一吼叫影格的 `sss-on.png` 和 `sss-off.png`，可直接比較臉部和背景。Three.js 報告核對八權重、尾巴物理開關前後的像素差，以及重新取樣能否得到相同結果。
- 移植到其他四足角色時，先改骨名、關節位置、`CageVertex` 和 UV 遮罩，重新量測腳掌著地及接縫，再調尾骨數量、相機和動作。此預設會驗證劍龍的分件、骨數、三角面數和九鏡長度。若改剪接，請一起更新共用時序、兩個擷取器及 [`delivery.py`](delivery.py) 的檢查條件，並準備對齊新時序的音軌。此流程的起點是具有對應骨架與網格的角色。
- [實測紀錄](verification.md) 列出歷史前腳修正的變形量，以及這份工作流程的重建與播放檢查。
