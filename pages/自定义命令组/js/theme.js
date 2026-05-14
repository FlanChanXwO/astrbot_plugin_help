/**
 * 主题管理模块 - 支持深色/浅色模式切换
 */

// 主题状态
let isDarkMode = false;
let themeChangeListeners = [];

/**
 * 初始化主题系统
 * 检测系统偏好并监听变化
 */
export function initTheme() {
    // 检测系统深色模式偏好
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    isDarkMode = mediaQuery.matches;

    // 监听系统主题变化
    mediaQuery.addEventListener('change', (e) => {
        setDarkMode(e.matches);
    });

    // 应用初始主题
    applyTheme();

    return isDarkMode;
}

/**
 * 设置深色模式
 * @param {boolean} dark - 是否启用深色模式
 */
export function setDarkMode(dark) {
    if (isDarkMode === dark) return;
    isDarkMode = dark;
    applyTheme();

    // 通知所有监听器
    themeChangeListeners.forEach((listener) => listener(isDarkMode));
}

/**
 * 应用主题到文档
 */
function applyTheme() {
    document.documentElement.setAttribute('data-theme', isDarkMode ? 'dark' : 'light');
    document.body.classList.toggle('dark-mode', isDarkMode);
}

/**
 * 获取当前主题状态
 * @returns {boolean} 是否为深色模式
 */
export function isDarkTheme() {
    return isDarkMode;
}

/**
 * 添加主题变化监听器
 * @param {Function} listener - 回调函数
 */
export function onThemeChange(listener) {
    themeChangeListeners.push(listener);
}

/**
 * 移除主题变化监听器
 * @param {Function} listener - 回调函数
 */
export function offThemeChange(listener) {
    themeChangeListeners = themeChangeListeners.filter((l) => l !== listener);
}
