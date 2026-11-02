# OrbitCluster 品牌素材

2026-11-02 按用户要求使用内置 **GPT Image / imagegen** 生成。不是 CLI/API fallback；不依赖额外 API key。

形象：开放轨道与三个计算节点，呼应 Orbit 和 Cluster。强调色 `#2563eb`，辅助蓝 `#60a5fa`，文字 `#20262d`。全部为透明背景的 PNG 位图；不是 SVG 或矢量源文件。

素材位于 `oa/cluster-portal/portalapp/static/brand/`：

| 文件 | 用途 |
|---|---|
| `orbitcluster-symbol-source.png` | GPT Image 标记原稿，保留生成结果 |
| `orbitcluster-logo.png` | GPT Image 横版标记与 OrbitCluster 字标 |
| `orbitcluster-mark.png` | 256px 标记，用于顶栏和登录页 |
| `favicon-16.png` / `favicon-32.png` | 浏览器标签图标 |
| `favicon.ico` | 包含 16/32/48/64px 的兼容图标 |
| `apple-touch-icon.png` | 180px 触屏收藏图标 |

PNG/ICO 尺寸导出由 ImageMagick 完成，保留透明通道，不重画标识。网页采用“图形标记 + 平台名称文本”，所以原有平台名称设置仍然有效；横版 Logo 可用于文档和其他品牌材料。

## 标记提示词（原文）

```text
Use case: logo-brand. Create ONE original production-ready logo symbol for OrbitCluster, a university research laboratory compute-cluster portal. This asset will be used as a 28px navigation logo and a 16px browser favicon. Symbol ONLY, NO text. Concept: an abstract open orbital O with three bold rounded compute-node blocks arranged into a compact, distinctive cluster, integrated with the orbital path; coherent silhouette, not a generic atom illustration or a network diagram. Flat vector-like brand design, confident geometric proportions, generous clear negative space, large solid shapes readable at tiny sizes. Palette: a lively clear cobalt blue near #2563EB; a restrained second blue #60A5FA is allowed on one node. No gradients, no glow, no shadows, no 3D, no mockup, no decorative stars, no letters, no watermark, no border or app tile. Square composition, centered mark filling about 82 percent of the canvas with consistent small padding. Genuinely transparent background; clean sharp alpha edges. Deliver one final symbol, not multiple alternatives or a contact sheet.
```

## 横版 Logo 提示词（原文）

输入为上一张生成的标记，作为保留图形的参考。

```text
Use case: logo-brand. The attached image is the approved symbol reference for OrbitCluster. Create a single horizontal logo lockup on a genuinely transparent background: the exact orbital three-node symbol at the left, and the exact word 'OrbitCluster' at the right. Preserve the symbol's silhouette, proportions and two blue colors. The wordmark must be clear modern humanist sans-serif, medium weight, color #20262D, exact capitalization OrbitCluster (O-r-b-i-t-C-l-u-s-t-e-r), no tagline or other text. Balanced optically: symbol height about 1.5 times the cap height; a comfortable gap between mark and word. Wide horizontal canvas with close but safe transparent padding; not a square presentation board. Crisp professional brand asset, flat vector-like finish. No background, no mockup, no shadow, no glow, no new shapes, no decoration, no watermark. Deliver only one complete horizontal logo.
```

提示词中的 approved 指同一图形在两个资产中的固定参考约束，不表示用户已经确认最终 Logo。网页确认后再制作正式指南截图。
