# 阶段 335 · 产品写图还在用 `cv2.imwrite`：中文路径下静默写空，却把路径当成功返回

> 日期 2026-10-26 · 性质：**产品缺陷修复**（334 审计候选①，本轮自己复现了才动手）

## 一、先复现，再动手（本机实测）

```
cv2.imwrite(C:\...\cn_路径_xxx\结果.png, arr)  ->  返回 False，且一个字节都没写
```

`cv2.imwrite` 在**非 ASCII 路径**上不是抛错，而是**返回 False 什么都不做**。
本仓早就知道这件事——`save_image()` 专门用 `imencode + open(path,'wb')` 绕开它
（`core/image_utils.py:680` 那句注释「避免中文路径问题」），`services/compare_service.py:389`
还把它写成显式规则「cv2.imwrite 不支持中文路径，这里统一走 save_image」。
**但规则没有覆盖整族**，还漏着两处产品路径。

## 二、两处（其中一处比审计说的更严重）

### ① `core/image_utils._save_avif()` 第三级回退

```python
cv2.imwrite(png_path, cv2.cvtColor(flat, cv2.COLOR_RGB2BGR))
return png_path          # ← 返回值不看，路径无条件交出去
```

后果链：上层 `file_service.save_result` 记「处理结果已保存」⇒ 调用方拿到 `status="success"`
与一个**不存在的路径**（画廊空白 / 下载 404，用户不知道为什么）。

⚠ 诚实说明可达性：本机 **走不到这条分支**——`cv2.imencode(".avif")` 确实抛错（分支①失败），
但 Pillow 12.3 **原生支持 AVIF** ⇒ 分支②成功。审计说「requirements.txt 里没有 pillow-avif-plugin ⇒
分支③必走」这句**不成立**（新版 Pillow 自带）。所以这是一条**条件性缺陷**：
AVIF 编码在两种后端都不可用的机器上才会暴露（旧 Pillow / 无 libavif 的构建）。
测试因此用 monkeypatch 把分支①②关掉来逼出分支③（LESSONS：注入反例不改工作树）。

### ② `services/cache_service._materialize_original()`（这条每天都可能咬人）

```python
cv2.imwrite(str(target), bgr)
return target
```

`UPLOAD_DIR` 在中文目录里（用户名含中文、或把缓存设到 `D:\图片\`）时**原图根本没落盘**，
而历史记录照样登记成成功 ⇒ 历史页「重新处理」要读的那张原图不存在。
这条与 AVIF 支不支持无关，**只要缓存目录含中文就每天发生**。

## 三、修法：不另写一份「怎么落盘」

两处都改成走本仓已有的 `core.image_utils.save_image(path, arr, fmt="PNG")`：它用 `imencode + open()`、
编码失败抛 `IOError`、支持中文路径。`cache_service` 里用**延迟导入**（避免 core ↔ services 的导入环），
并删掉那个现在没用的 `import cv2` 依赖检查（`save_image` 自己会检查）。

顺带把「绝不因格式崩溃」这句承诺的边界写清：AVIF 不可用时**照样降级 PNG**，不因格式抛错；
新抛的是**编码/写盘真失败**——那种情况下返回路径就是撒谎（测试
`test_avif_fallback_raises_when_png_encode_fails` 钉住这条新契约）。

## 四、守卫（以及我自己在这里踩的两次坑）

`tests/test_avif_fallback335.py` 六条：分支③在 ASCII 与中文目录都要**真的写出能读回来的图**、
编码失败要抛错、缓存原图在中文目录要落盘，再加一条**结构守卫**：
`core/services/ui/utils` 里产品代码不得出现 `cv2.imwrite`（`__main__` 自检除外），目录**机械枚举**、不手抄文件名。

两次自我打脸，都记下来（这才是本阶段最值得留的东西）：

1. **判据写错 ⇒ 误报 4 处**。第一版用 `" == " in ast.dump(node.test)` 识别 `if __name__ == "__main__":`，
   但 `ast.dump` 里没有 `" == "`（比较是 `ast.Eq(...)`）⇒ `__main__` 子树**一个都没排除**，
   把 `file_service`/`export_service`/`quality_service`/`validators` 四处**纯自检**调用报成产品缺陷。
   差一点就为了过自己的守卫去改四个不该改的文件。**写守卫时要连守卫自己一起验**（#173/#176 的元缺陷族）。
2. **验证通道用错 ⇒ 测试红在产品之外**。我的读回断言用了 `cv2.imread`，它自己就因为中文路径返回
   `None` ⇒ `test_..._chinese_path` 红在一件与产品无关的事上。改成用**被测系统自己的** `load_image` 读回。
   判据：**探针要走在用户实际走的通道上**，否则测的是自己的探针。

## 五、反例闭合

`git worktree add --detach temp/wt335 HEAD`（HEAD 是修复前）跑同一套 6 条 ⇒

```
修复前 rc=1  FAILED 4 / 6
   test_avif_fallback_writes_a_real_file_chinese_path
   test_avif_fallback_raises_when_png_encode_fails
   test_materialize_original_writes_file_under_chinese_dir
   test_repo_has_no_product_cv2_imwrite_left
修复后 rc=0  6 passed；定向回归（image_utils + cache + 历史 + 334 批量键）77 passed
```

修复前也绿的那两条是 ASCII 路径的正常行为——**它们本来就该绿**，留着当「别把好的改坏」的锚。
