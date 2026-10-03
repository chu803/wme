# 135 · file_service 真值审计——缩略图形状归一化与退化尺寸守卫

> 日期：2026-09-26 ｜ 对象：`services/file_service.py`（未审榜榜首，荒漠度 5.4 = 103 代码行 ÷ 19 引用测试 / 2 文件）
> 方法：探针量化 → 分类（defect vs by design）→ 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记

## 一、探针量化（%TEMP%/s135_probe.py，项目内零残留）

| # | 输入 | 修复前行为 | 判定 |
|---|---|---|---|
| P1 | 灰度 2D `(200,300)` | `ValueError: could not broadcast (85,128) → (85,128,3)`（`canvas[y0:y0+nh, x0:x0+nw] = resized` 深层崩溃） | **defect D1** |
| P2 | RGBA 4C `(200,300,4)` | `ValueError: could not broadcast (85,128,4) → (85,128,3)` | **defect D2** |
| P3 | 退化尺寸 `(0,100,3)` | OpenCV 断言崩溃（`resize.cpp Assertion failed`，报错不透明、不可读） | **defect D3** |
| P4 | 正常 RGB 对照 | 形状/居中/比例全对 | by design |
| P5 | `save_result(fmt=None)` | 走 `config.DEFAULT_OUTPUT_FORMAT`，正常 | by design |
| P6 | `save_result(fmt='jpg')` | `.upper()` 归一，正常 | by design |
| P7 | `save_upload(original_name='')` | 回退 `upload.png`，正常 | by design |

## 二、归因

`make_thumbnail` 是「**视频抽帧**（cache_service:395）/ **PDF 首页渲染**（cache_service:464）/ **历史重处理**（tab_history:433）」三条真实调用路径的唯一形状防线，而这三处的数组全部来自**外部解码器**，通道形态不受本模块控制：

- 崩溃后果被 `cache_service` 的 `except Exception → return None`（:494–496）**放大为整条历史记录登记流产**——与该函数 docstring 明文承诺「若首页渲染失败，则回退到纯色占位缩略图，**保证记录始终可注册**」（:452）直接相悖：该兜底只覆盖 `render_pdf_first_page` 返回 `None`，不覆盖 `make_thumbnail` 抛异常。
- 对照：`pdf_service._page_to_array`（:265–270）自身做灰度补三通道 / 丢 alpha 防御——**边界处归一化是项目既有范式**，`make_thumbnail` 缺了这一层。
- 附带发现（**登记不修**，属 video 侧审计范围）：`video_service.py:210` 对帧做 `cv2.cvtColor(frame, COLOR_BGR2RGB)`，若某后端对灰度视频返回 2D 帧将同类崩溃；若发生则同样落在 `make_thumbnail` 之外的抽帧处。

## 三、最小修复（services/file_service.py）

1. 新增 `_as_rgb3(arr)`：2D 灰度 → 三通道复制；3C 原样；4C RGBA → 丢 alpha（与 `load_image` 合同一致）；其他形状 → 清晰 `ValueError`（带实际 shape）。
2. `make_thumbnail` 开头接入 `_as_rgb3` + 退化尺寸显式守卫：`h==0 or w==0 → ValueError("空图像…")`（把 OpenCV 不透明断言换成可读契约错误；**不**静默返回灰方块，避免掩盖上游 bug）。
3. docstring 同步：参数说明改为「接受灰度(H×W)/RGB(H×W×3)/RGBA(H×W×4)」，新增「异常」段。
4. 模块自检补用例 4：灰度/RGBA 归一化 + 退化尺寸必须抛 `ValueError`。

## 四、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_file_service_audit135.py`（5 函数 8 用例：灰度复制/RGBA 丢 alpha/退化尺寸×3/不支持形状/RGB 对照不回归） | ✅ 8/8 绿 |
| 关联既有测试（test_input_normalization / test_pdf_history） | ✅ 62/62 绿，rc=0 |
| 注入式反例（`%TEMP%/s135_inject.py`：外部备份 → LF 归一化 → 注入 BUGGY 移除 `_as_rgb3` 调用与退化守卫） | BUGGY 下守卫 **rc=1 红**；还原后 sha256=`6a2ca94c63819c9f` **逐字节一致**；回绿 rc=0 ✅ |
| 模块自检实跑（含新用例 4） | ✅ rc=0 |
| 子进程守卫登记 `tests/test_legacy_selfcheck.py` | ✅ `("services/file_service.py", [])`（自检非破坏性：只写 uploads/outputs 运行时目录、`make_unique_path` 不覆盖、只删自身 tmp） |
| `audit_selector.py --check` | ✅ EXIT=0，登记 34 条 |

## 五、登记

- `scripts/audit_selector.py` `AUDITED_SINCE` += `"services/file_service": 135`。
- 下一站（`--unaudited`）：`services/video_export` 5.4 → `utils/i18n` 5.2 → `ui/components` 4.8 → `ui/tab_history` 4.8。
- 待办登记：`video_service` 灰度视频帧转换潜在 2D 崩溃（见归因末条）。

## 六、经验

见 `LESSONS.md` 第 149 条：服务层「数组防线」函数的崩溃会被上游宽 except 放大为**整条业务动作流产**——修复目标是「归一化 + 清晰契约错误」，而非让调用方再加兜底。
