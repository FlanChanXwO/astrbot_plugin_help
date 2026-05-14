/**
 * API 调用模块
 */

const bridge = typeof window !== 'undefined' ? window.AstrBotPluginPage : null;

/**
 * 等待 bridge 就绪
 * @returns {Promise<Object>}
 */
export async function ready() {
    if (!bridge) {
        throw new Error('AstrBotPluginPage bridge not available');
    }
    return await bridge.ready();
}

/**
 * 获取自定义分组列表
 * @returns {Promise<Array>}
 */
export async function getCustomGroups() {
    try {
        const result = await bridge.apiGet('custom-groups');
        // Handle both formats: {success: true, data: []} and direct array response
        if (Array.isArray(result)) {
            return result;
        }
        if (result && result.success) {
            return result.data || [];
        }
        if (result && result.error) {
            throw new Error(result.error);
        }
        // If result is an object but not in expected format, return empty array
        return [];
    } catch (err) {
        throw new Error(err.message || '未知错误');
    }
}

/**
 * 创建自定义分组
 * @param {Object} groupData - 分组数据
 * @returns {Promise<Object>}
 */
export async function createCustomGroup(groupData) {
    try {
        const result = await bridge.apiPost('custom-groups/create', groupData);
        // Handle both formats: {success: true, ...} and direct boolean/empty response
        if (result === true || result === null || result === undefined) {
            return {success: true};
        }
        if (result && result.success) {
            return result;
        }
        if (result && result.error) {
            throw new Error(result.error);
        }
        // If no explicit success/error, assume success
        return {success: true};
    } catch (err) {
        throw new Error(err.message || '创建失败');
    }
}

/**
 * 更新自定义分组
 * @param {number} index - 分组索引
 * @param {Object} groupData - 分组数据
 * @returns {Promise<Object>}
 */
export async function updateCustomGroup(index, groupData) {
    try {
        const result = await bridge.apiPost('custom-groups/update', {
            index: index,
            group: groupData,
        });
        if (result === true || result === null || result === undefined) {
            return {success: true};
        }
        if (result && result.success) {
            return result;
        }
        if (result && result.error) {
            throw new Error(result.error);
        }
        return {success: true};
    } catch (err) {
        throw new Error(err.message || '更新失败');
    }
}

/**
 * 删除自定义分组
 * @param {number} index - 分组索引
 * @returns {Promise<Object>}
 */
export async function deleteCustomGroup(index) {
    try {
        const result = await bridge.apiPost('custom-groups/delete', {index: index});
        if (result === true || result === null || result === undefined) {
            return {success: true};
        }
        if (result && result.success) {
            return result;
        }
        if (result && result.error) {
            throw new Error(result.error);
        }
        return {success: true};
    } catch (err) {
        throw new Error(err.message || '删除失败');
    }
}

/**
 * 加载并渲染分组列表
 * @param {HTMLElement} container - 分组列表容器
 * @param {Array} groups - 分组数据
 * @param {Function} escapeHtml - HTML转义函数
 * @param {Function} onGroupClick - 点击分组回调
 */
export function renderGroupsList(container, groups, escapeHtml, onGroupClick) {
    if (groups.length === 0) {
        container.innerHTML = `
      <div class="empty-state">
        <svg class="empty-state-icon" width="64" height="64" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
          <path d="M337.487 213.925c-6.537-6.547-15.571-10.598-25.553-10.598s-19.018 4.050-25.553 10.597l-256.357 256.383c-6.547 6.546-10.597 15.589-10.597 25.579 0 9.989 4.050 19.034 10.597 25.579l256.343 256.343c7.065 7.065 16.313 10.596 25.547 10.596s18.483-3.532 25.547-10.596c14.129-14.129 14.129-37.029 0-51.158l-230.72-230.758 230.733-230.797c6.555-6.547 10.61-15.596 10.61-25.591 0-9.989-4.050-19.034-10.597-25.579zM997.484 470.308l-256.343-256.382c-6.467-6.134-15.228-9.906-24.869-9.906-19.975 0-36.17 16.193-36.17 36.17 0 9.654 3.782 18.425 9.946 24.911l230.718 230.781-230.733 230.784c-6.551 6.546-10.604 15.593-10.604 25.585 0 19.969 16.183 36.159 36.149 36.17 9.236 0 18.484-3.532 25.548-10.596l256.343-256.343c6.558-6.545 10.615-15.595 10.615-25.591 0-9.99-4.052-19.035-10.603-25.579zM609.667 82.453c-2.506-0.624-5.381-0.981-8.34-0.981-17.042 0-31.313 11.868-34.999 27.789l-175.627 756.609c-0.583 2.435-0.917 5.231-0.917 8.105 0 20.022 16.229 36.255 36.252 36.259 17.063-0.034 31.356-11.861 35.158-27.763l175.499-756.59c0.598-2.463 0.942-5.292 0.942-8.199 0-17.066-11.82-31.372-27.719-35.177z" fill="#cbd5e0"/>
        </svg>
        <p>暂无自定义命令组</p>
        <p style="font-size: 13px; margin-top: 8px;">点击「新建分组」开始创建</p>
      </div>
    `;
        return;
    }

    container.innerHTML = groups.map((group, index) => `
    <div class="group-card" data-index="${index}">
      <div class="group-card-header">
        <span class="group-card-title">${escapeHtml(group.group_name)}</span>
        <span class="group-card-badges">
          ${group.hidden ? `<span class="group-card-hidden">
            <svg viewBox="0 0 1024 1024" width="14" height="14" fill="#94a3b8"><path d="M215.552 809.984L802.304 187.904c9.216-9.728 24.576-10.24 34.304-1.024 9.728 9.216 10.24 24.576 1.024 34.304L250.368 843.264c-9.216 9.728-24.576 10.24-34.304 1.024-9.216-9.216-9.728-24.576-0.512-34.304z"/><path d="M183.296 509.44c-2.048-5.12-2.56-19.968-1.024-25.6 111.616-147.968 228.864-219.136 348.672-211.456 43.008 2.56 82.944 15.872 119.296 34.304l34.304-36.352c-45.056-24.576-95.744-42.496-150.528-45.568C396.8 215.04 265.216 293.376 142.336 456.192c-13.824 18.432-10.752 60.416-1.536 76.8 15.872 28.16 75.264 92.672 162.816 140.8l34.304-36.352c-84.48-43.52-141.824-105.472-154.624-128zM884.736 460.8c-18.432-32.768-70.144-100.864-144.384-155.648l-33.792 35.84c72.192 52.224 121.856 118.272 135.68 142.336 1.536 4.608 3.072 19.456 2.56 26.624-109.568 126.464-230.4 184.32-358.912 172.032-29.696-3.072-57.856-9.216-83.968-17.92l-35.84 38.4c35.328 13.824 74.24 24.064 115.2 27.648 13.312 1.024 26.112 2.048 38.912 2.048 131.584 0 253.952-65.024 364.032-193.536 15.872-18.944 6.656-66.56 0.512-77.824z"/><path d="M390.656 477.184c0 28.672 10.24 54.784 26.624 75.776l33.792-35.84c-7.68-11.776-11.776-25.088-11.776-39.936 0-40.448 32.768-73.728 73.728-73.728 13.312 0 25.6 3.584 36.352 9.728l33.792-35.84c-19.968-13.824-44.032-22.528-70.144-22.528-67.584 0.512-122.368 55.296-122.368 122.368zM586.24 477.184c0 40.448-32.768 73.728-73.728 73.728-1.536 0-2.56-0.512-4.096-0.512l-38.4 40.96c13.312 5.12 27.648 7.68 42.496 7.68 67.072 0 121.856-54.784 121.856-121.856 0-17.408-3.584-34.304-10.752-49.152l-38.912 40.96c1.024 3.072 1.536 5.632 1.536 8.192z"/></svg>
        </span>` : ''}
          <span class="group-card-priority">优先级 ${group.priority || 0}</span>
        </span>
      </div>
      <div class="group-card-desc">${escapeHtml(group.description || '无描述')}</div>
      <div class="group-card-meta">
        <span class="group-card-commands">${(group.commands || []).length} 个命令</span>
      </div>
    </div>
  `).join('');

    container.querySelectorAll('.group-card').forEach((card) => {
        card.addEventListener('click', () => {
            const index = parseInt(card.dataset.index);
            onGroupClick(index);
        });
    });
}
