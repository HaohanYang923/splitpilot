# SplitPilot 项目总汇（PayPal AI Hackathon 2026）

汇总时间：2026-11-07。当前版本 v4。这一份文档把比赛信息、产品、
验证结果和剩余步骤全部合在一起，其余细节见同目录 README.md、
SUBMISSION.md、DEMO_SCRIPT.md。

## 1. 比赛事实（已在官方 Devpost 页核实）

- 比赛：PayPal AI Hackathon，paypalaihackathon.devpost.com，线上
- 硬性要求：项目必须同时有意义地使用 PayPal（Sandbox 环境）和 AI
- 截止：2026-11-12 15:00 EST，即太平洋时间 11-12 中午 12:00
- 奖池：总计 $69,750，现金类别 $67,500+；一等奖 $12,000、二等
  $8,000、三等 $5,000；单项奖各 $5,000（Most Creative、Most
  Impactful、Best Demo Delivery、Best Use of PayPal + AI、Best Use
  of Agentic Commerce）；赞助奖 Best Use of AG Grid 一等 $5,000 /
  二等 $2,000 / 三等 $1,000，另有 APIMatic、Bryntum、Channel3、
  Render 等
- 提交物：文字描述、可运行的 demo（托管链接或仓库运行说明）、
  使用工具说明、公开代码仓（带 license 置顶）、3 分钟以内公开
  YouTube 演示视频
- 评审维度：Technological Implementation、Design、Potential
  Impact、Innovation/Idea、Presentation

## 2. 产品

SplitPilot：把一张混乱的团体账单变成公平分账，并通过 PayPal 真正
把钱收回来。粘贴账单、告诉它谁在场、谁垫付了全款，剩下的解析、
分账、查错、结算、收款、催款都由它完成。

目标奖项：Best Use of PayPal + AI、Best Use of Agentic Commerce、
Most Impactful、Best Use of AG Grid。

## 3. 功能清单

- AI 账单解析：逐行识别菜品、价格、数量（Coffee x2）、价格在行首
  的写法、括号点名；没点名的自动全员平分
- 公平分账：税和小费按每人小计比例分摊，舍入对齐到分，各人份额
  之和精确等于账单总额
- AI 异常检查（收款前）：重复收费、读不出价格的行、账单 Total
  与明细合计对不上（报出差额）
- 垫付人净额结算：垫付人份额自动结清，其余人欠垫付人
- 逐人解释：每人的每个数字如何算出来，可审计
- PayPal 收款全流程：Orders v2 API（Sandbox）建单 → 官方审批
  链接 → Capture 收款，页面有实时收款进度条
- 每人专属付款页：随机 token 链接 /pay/<token>，对方只看到自己
  的菜品和金额（金额由服务器定，改不了），可直接付款
- 催款文案：礼貌 / 强硬 / 搞笑三档，按本人菜品和金额生成
- AG Grid 结果表：可排序、可筛选、按币种格式化；CDN 不可达时
  自动退回普通表格，不白屏
- 场景预设：聚餐、自驾游、合租水电一键切换
- 多币种：USD / CAD / EUR / GBP，解析认 € / £，币种真正写入
  PayPal 订单
- 状态落盘：分账和收款写入 data/state.json，服务器重启不丢
- 导出：一键复制群聊版总结、下载 CSV
- 部署：render.yaml 一键托管；GitHub Actions 自动跑测试
- 无凭证模式：明确标注的 Sandbox Demo 模式，同 payload 模拟全
  流程，评委离线也能评；绝不冒充真实订单

## 4. 技术

- Python 3 标准库单文件 app.py（约 37K），零依赖、零构建：
  python3 app.py 即跑，http://localhost:8000
- PayPal：OAuth client-credentials + Orders v2，凭证走环境变量
  PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET（.env.example 有模板）
- AI 层为本地确定性实现（解析、分账、异常检测、解释、催款），
  无需任何 API key
- 测试：tests/test_split.py，12 个用例

## 5. 设计（v4）

按 frontend-design（Anthropic 官方版）与 ui-ux-pro-max 两个
skill 重做：方向是“被认真对待的纸质小票”。扁平、无阴影无
渐变；IBM Plex Sans 正文、IBM Plex Mono 承载所有数字；颜色
只表达钱的状态（PayPal 蓝 = 行动，绿 = 已结清，红 = 欠款）；
付款状态是倾斜的橡皮章徽章；虚线是撕票线。按钮 ≥44px、键盘
焦点可见、对比度达标、尊重减少动画偏好。付款页同设计系统。

## 6. 验证证据（全部实测，非自述）

- 12 个单元测试全过（解析、分账求和、结算、币种、token、模拟
  capture 等）
- 示例账单 $83.16 → Alice $27.72 / Bob $37.80 / Carol $17.64，
  合计精确 $83.16；Alice 垫付时应收回 $55.44
- 完整流程通过：分账 → 打开 Bob 专属付款页 → 从付款页建单 →
  capture → 状态变 Paid、已收 $37.80
- 重启恢复通过：杀掉服务器重启后应收、已收、付款链接全保留
- 总额对不上时警告准确报出差额（实测 $5.99）
- AG Grid 真实渲染已截图确认（docs/screenshot.png，README 首页）

## 7. 文件清单（16 个）

app.py（主程序）、tests/test_split.py、README.md、SUBMISSION.md
（Devpost 文案 + 提交清单）、DEMO_SCRIPT.md（2:45 演示脚本）、
PROJECT_SUMMARY.md（本文档）、LICENSE（MIT）、render.yaml、
.env.example、.gitignore、.github/workflows/tests.yml、
docs/screenshot.png。打包：splitpilot-v4.zip。

## 8. 评审维度对照

- Technological Implementation：真实 Orders v2 全流程、状态
  持久化、零依赖可运行、CI
- Design：小票设计系统、AG Grid、橡皮章状态、双页一致
- Potential Impact：真实场景（聚餐 / 出行 / 合租），付款页把
  工具变成可分发的产品
- Innovation：收款前 AI 查错、逐人可审计解释、垫付净额结算
- Presentation：2:45 脚本含查错、真审批链接、付款页、网格
  排序四个记忆点；README 带截图

## 9. 还剩什么（都需要用户本人账号操作）

1. Devpost 注册并加入比赛（约 5 分钟）
2. ~~developer.paypal.com 建 Sandbox 应用拿凭证~~ 已完成
   （2026-11-07，凭证已用于第 10 节的真实验证）
3. 把项目推到公开 GitHub 仓库（license 已在根目录）
4. 按 DEMO_SCRIPT.md 录 3 分钟内视频，传 YouTube 公开，回
   Devpost 粘 SUBMISSION.md 的文案提交
建议 11-11 前提交，截止是 11-12 中午 PST。

## 10. 风险与未验证项

- 真实 Sandbox 已验证（2026-11-07，用用户本人凭证）：OAuth
  拿 token 成功，经 app 本身建单成功（订单 0CP09533JW8238807，
  状态 CREATED，审批链接为真实 sandbox.paypal.com 链接）。
  唯一没法代验的是买家在 PayPal 页面点批准之后的 capture，
  需要录制时用 Sandbox 买家账号批准一次；capture 的代码路径
  与建单同一套，且演示模式下全流程已测通
- 实测发现 Sandbox 的 token 接口当时非常慢（单次约 30 秒，
  偶发断连）：已修复，token 改为进程内缓存复用（有效期数
  小时）、超时放宽到 60 秒、传输错误自动重试一次、建单带
  PayPal-Request-Id 幂等键防重复单。首次建单约 75 秒，后续
  走缓存会快很多；录制时先随便建一单热身再正式录
- AG Grid 走 CDN：托管环境正常加载；离线时有表格兜底
- 状态是单机单文件存储：hackathon 演示够用，不是生产级方案
