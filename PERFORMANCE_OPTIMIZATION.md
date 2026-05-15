# Help Menu Performance Optimization Plan

## Performance Bottlenecks Identified

### 1. Render Serialization (Major Bottleneck)
**Location:** `src/infrastructure/rendering/html_renderer.py:35`
```python
self._render_semaphore = asyncio.Semaphore(1)  # Only 1 concurrent render
```

**Impact:** Only one help menu can be rendered at a time, causing queuing delays.

### 2. Fixed Wait Time
**Location:** `src/infrastructure/rendering/html_renderer.py:216`
```python
await page.wait_for_timeout(500)  # Fixed 500ms wait per render
```

**Impact:** Every render adds 500ms delay regardless of actual load time.

### 3. Browser Startup Overhead
**Impact:** Playwright browser startup is expensive (1-3 seconds).

### 4. Page Creation Overhead
**Location:** `src/infrastructure/rendering/html_renderer.py:209`
```python
page = await browser.new_page()  # New page per render
```

## Optimization Strategies

### Quick Wins (Easy to Implement)

#### 1. Increase Concurrent Rendering
**File:** `src/infrastructure/config/datamodels.py`
**Change:** Increase `max_concurrent_tasks` from 1 to 3

```python
max_concurrent_tasks: int = Field(
    default=3,  # Changed from DefaultCFG.LIMIT_TASK
    description="最大并发渲染数"
)
```

**File:** `src/infrastructure/rendering/html_renderer.py`
**Change:** Update semaphore to use config value
```python
self._render_semaphore = asyncio.Semaphore(self.config.rendering.max_concurrent_tasks)
```

**Expected Improvement:** 3x faster for multiple concurrent help requests.

#### 2. Optimize Wait Time
**File:** `src/infrastructure/rendering/html_renderer.py:216`
**Change:** Reduce from 500ms to 200ms or use dynamic wait

```python
# Option A: Reduce fixed wait
await page.wait_for_timeout(200)  # Reduced from 500ms

# Option B: Use dynamic wait (more complex but better)
await page.wait_for_load_state("networkidle", timeout=1000)
```

**Expected Improvement:** 300ms faster per render.

#### 3. Use T2i Rendering (Fastest)
**File:** `_conf_schema.json`
**Change:** Enable t2i by default

```json
{
  "rendering": {
    "use_t2i": true,
    "jpeg_quality": 90
  }
}
```

**Expected Improvement:** 2-5x faster (t2i is optimized for batch rendering).

### Medium-Term Optimizations

#### 4. Browser Pool Reuse
**File:** `src/infrastructure/rendering/html_renderer.py`
**Concept:** Reuse browser and pages instead of creating new ones

```python
async def _get_page(self):
    """Get or reuse page from pool"""
    if not hasattr(self, '_page_pool'):
        self._page_pool = []
        self._max_page_pool_size = 3

    if self._page_pool:
        page = self._page_pool.pop()
        # Clear previous content
        await page.set_content("<html><body></body></html>")
        return page

    browser = await self._get_browser()
    page = await browser.new_page()
    return page

async def _return_page(self, page):
    """Return page to pool for reuse"""
    if len(self._page_pool) < self._max_page_pool_size:
        self._page_pool.append(page)
    else:
        await page.close()
```

**Expected Improvement:** 500ms-1s faster (avoid browser startup).

#### 5. Pre-render Common Help Menus
**File:** `src/application/services/help_service.py`
**Concept:** Render and cache help menus during plugin initialization

```python
async def initialize(self):
    """Initialize service and pre-render common help menus"""
    self._init_prefixes()

    # Pre-render common help menus in background
    asyncio.create_task(self._pre_render_common_menus())

    logger.info("Initialization completed")

async def _pre_render_common_menus(self):
    """Pre-render frequently accessed help menus"""
    common_queries = ["", "help", "status"]  # Common searches

    for query in common_queries:
        try:
            cache_key = self._get_cache_key("command", query, False)
            # Check if already cached
            if await self.cache.get_cached_image(cache_key):
                continue

            # Render and cache
            await self._render_with_html(
                analyzer=self.command_analyzer,
                title="Astrbot 指令帮助",
                query=query,
                allowed_plugins=None,
                cache_key=cache_key,
            )
            logger.info(f"Pre-rendered help menu for query: '{query}'")
        except Exception as e:
            logger.warning(f"Failed to pre-render help menu for '{query}': {e}")
```

**Expected Improvement:** First help menu request is instant (already cached).

### Long-Term Optimizations

#### 6. Implement Progressive Rendering
**Concept:** Show a text preview immediately, then replace with image

```python
async def show_help(self, event, query="", is_admin=False):
    """Display help menu with progressive loading"""

    # 1. Send text preview immediately (fast)
    text_preview = self._generate_text_preview(query)
    yield event.plain_result(f"📋 {text_preview}")

    # 2. Render image in background
    try:
        result = await self._render_with_html(...)
        if result:
            # Send image and delete text preview
            yield event.chain_result([Image.fromFileSystem(result)])
    except Exception as e:
        yield event.plain_result(f"图片渲染失败: {str(e)}")
```

**Expected Improvement:** User gets immediate feedback, image loads asynchronously.

#### 7. Add Render Progress Indicators
```python
async def show_help(self, event, query="", is_admin=False):
    """Display help menu with progress indicators"""

    yield event.plain_result("🔄 正在生成帮助菜单...")

    if query:
        yield event.plain_result(f"🔍 搜索命令: {query}")

    # Render...
    result = await self._render_with_html(...)

    yield event.chain_result([Image.fromFileSystem(result)])
    yield event.plain_result("✅ 帮助菜单已生成")
```

**Expected Improvement:** Better user experience during long renders.

## Recommended Implementation Order

### Phase 1: Quick Wins (Do Now)
1. ✅ Enable t2i rendering by default
2. ✅ Increase concurrent rendering to 3
3. ✅ Reduce fixed wait time to 200ms

**Expected Speedup:** 3-5x faster

### Phase 2: Browser Optimization (Next Sprint)
1. Implement browser/page pooling
2. Add pre-rendering for common queries
3. Optimize Playwright startup

**Expected Speedup:** 2-3x faster on top of Phase 1

### Phase 3: UX Improvements (Future)
1. Progressive rendering with text preview
2. Progress indicators
3. Caching strategy improvements

**Expected Outcome:** Near-instant perceived performance

## Configuration Changes

Add to `_conf_schema.json`:

```json
{
  "rendering": {
    "use_t2i": true,
    "jpeg_quality": 90,
    "max_concurrent_tasks": 3,
    "timeout_analysis": 30.0,
    "enable_pre_rendering": true,
    "pre_render_queries": ["", "help", "status"]
  }
}
```

## Performance Targets

| Scenario | Current | After Phase 1 | After Phase 2 | After Phase 3 |
|----------|---------|---------------|---------------|---------------|
| First help menu (no cache) | 5-10s | 1-2s | 0.5-1s | Instant + async image |
| Cached help menu | 1-2s | 0.5-1s | 0.5-1s | Instant |
| Concurrent requests | Queued | Parallel | Parallel | Parallel |
| Perceived wait time | 5-10s | 1-2s | < 1s | Instant |

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|------------|
| Enable t2i by default | Low | Already supported, just change default |
| Increase concurrent tasks | Low | Configurable, can revert if issues |
| Reduce wait time | Medium | May cause incomplete renders, test thoroughly |
| Browser pooling | Medium | Complex to implement, requires thorough testing |
| Pre-rendering | Low | Background task, failures are non-blocking |

## Testing Checklist

After implementing optimizations:
- [ ] Test with 3 concurrent help menu requests
- [ ] Verify t2i rendering quality is acceptable
- [ ] Check memory usage with browser pooling
- [ ] Test pre-rendering doesn't slow down startup
- [ ] Measure actual render times before/after
- [ ] Test with large command sets (100+ commands)
