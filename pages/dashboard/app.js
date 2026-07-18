/**
 * 应用入口 - 直接创建响应式 store 并作为根作用域
 */
import {
    createCustomGroup,
    deleteCustomGroup,
    getCommands,
    getCustomGroups,
    previewDeleteCustomGroup,
    ready,
    updateCommandPolicy,
    updateCustomGroup,
} from './js/api.js';
import {initTheme} from './js/theme.js';

let toastTimer = null;
let cmdKey = 0;

function newCommand(type = 'command') {
    return {
        _key: cmdKey++,
        type,
        command: '',
        description: '',
        pattern: '',
        aliases: [],
        examples: [],
        permission_level: 'normal',
        delegation_policy: 'normal',
        history_mode: 'command',
        hidden: false,
        linked_plugin: '',
        availability: 'available',
        sub_commands: [],
    };
}

// Create reactive store
const store = PetiteVue.reactive({
    // State
    groups: [],
    catalog: {items: [], total: 0, page: 1, page_size: 20},
    catalogQuery: '',
    panelVisible: false,
    panelExpanded: false,
    editingIndex: -1,
    toast: {show: false, message: '', type: 'success'},
    dialog: {show: false, title: '', message: '', okText: '确定', okClass: 'btn-danger', resolve: null},
    form: {
        currentGroupName: '',
        groupName: '',
        groupDesc: '',
        priority: 0,
        hidden: false,
        commands: [newCommand()],
    },

    // Draft data for create mode (to prevent accidental loss)
    draft: null,

    // Actions
    async loadGroups() {
        try {
            const result = await getCustomGroups();
            const data = Array.isArray(result) ? result : [];
            this.groups = data;
        } catch (err) {
            this.showToast('加载分组失败: ' + err.message, 'error');
        }
    },

    openPanel(index = -1) {
        this.editingIndex = index;
        if (index >= 0) {
            const g = this.groups[index];
            this.form.currentGroupName = g.group_name || '';
            this.form.groupName = g.group_name || '';
            this.form.groupDesc = g.description || '';
            this.form.priority = g.priority || 0;
            this.form.hidden = g.hidden || false;
            this.form.commands = (g.commands || []).map((c) => ({
                ...c,
                _key: cmdKey++,
                description: (c.description || '').trim(),
                aliases: [...(c.aliases || [])],
                examples: [...(c.examples || [])],
                sub_commands: [...(c.sub_commands || [])],
                permission_level: c.permission_level || (c.is_admin ? 'admin' : 'normal'),
                delegation_policy: c.delegation_policy || (c.is_admin ? 'sensitive' : 'normal'),
                history_mode: c.history_mode || 'command',
                linked_plugin: c.linked_plugin || '',
                availability: c.availability || 'available',
            }));
        } else {
            this._restoreDraft();
        }
        this.panelVisible = true;
    },

    _saveDraft() {
        this.draft = {
            groupName: this.form.groupName,
            groupDesc: this.form.groupDesc,
            priority: this.form.priority,
            hidden: this.form.hidden,
            commands: this.form.commands.map(c => ({
                ...c,
                aliases: [...c.aliases],
                examples: [...c.examples],
                sub_commands: [...(c.sub_commands || [])],
            })),
        };
    },

    _restoreDraft() {
        if (this.draft) {
            const d = this.draft;
            this.form.groupName = d.groupName;
            this.form.groupDesc = d.groupDesc;
            this.form.priority = d.priority;
            this.form.hidden = d.hidden;
            this.form.commands = d.commands.map(c => ({
                ...c,
                _key: cmdKey++,
                aliases: [...c.aliases],
                examples: [...c.examples],
                sub_commands: [...(c.sub_commands || [])],
            }));
            this.showToast('已恢复上次未保存的表单', 'success', 2000);
        } else {
            this.resetForm();
        }
    },

    _clearDraft() {
        this.draft = null;
    },

    closePanel() {
        if (this.editingIndex < 0) {
            this._saveDraft();
        }
        this.panelVisible = false;
        this.editingIndex = -1;
    },

    toggleExpand() {
        this.panelExpanded = !this.panelExpanded;
    },

    resetForm() {
        // 重置表单数据（不显示确认对话框）
        this.form.groupName = '';
        this.form.currentGroupName = '';
        this.form.groupDesc = '';
        this.form.priority = 0;
        this.form.hidden = false;
        this.form.commands = []; // 默认不给空命令
    },

    async confirmAndReset() {
        // 重置按钮点击时显示确认对话框
        const ok = await this.showConfirm('确定要重置表单吗？这将清除所有输入的内容。', '重置表单', '重置', 'btn-danger');
        if (!ok) return;
        this._clearDraft();
        this.resetForm();
        this.showToast('表单已重置', 'success', 2000);
    },

    addCommand() {
        this.form.commands.push(newCommand());
    },

    removeCommand(i) {
        this.form.commands.splice(i, 1);
    },

    showToast(msg, type = 'success', duration = 3000) {
        if (toastTimer) clearTimeout(toastTimer);
        this.toast.show = true;
        this.toast.message = msg;
        this.toast.type = type;
        toastTimer = setTimeout(() => {
            this.toast.show = false;
        }, duration);
    },

    async showConfirm(message, title = '确认', okText = '确定', okClass = 'btn-danger') {
        this.dialog.title = title;
        this.dialog.message = message;
        this.dialog.okText = okText;
        this.dialog.okClass = okClass;
        this.dialog.show = true;

        return new Promise((resolve) => {
            this.dialog.resolve = (result) => {
                this.dialog.show = false;
                resolve(result);
            };
        });
    },

    async save() {
        if (!this.form.groupName.trim()) {
            this.showToast('请输入分组名称', 'error');
            return;
        }
        for (const cmd of this.form.commands) {
            if (cmd.permission_level === 'admin' && cmd.delegation_policy === 'normal') {
                cmd.delegation_policy = 'sensitive';
            }
            if (cmd.type === 'command' && !cmd.command.trim()) {
                this.showToast('请填写命令名称', 'error');
                return;
            }
            if (cmd.type === 'regex' && !cmd.pattern.trim()) {
                this.showToast('请填写正则匹配模式', 'error');
                return;
            }
            if (['sensitive', 'forbidden'].includes(cmd.delegation_policy) && cmd.history_mode === 'full') {
                this.showToast('敏感或禁止委托的命令不能记录完整参数', 'error');
                return;
            }
        }
        const cmdList = this.form.commands.map((cmd) => {
            const base = {
                permission_level: cmd.permission_level,
                delegation_policy: cmd.delegation_policy,
                history_mode: cmd.history_mode,
                linked_plugin: (cmd.linked_plugin || '').trim() || null,
                availability: cmd.availability,
                hidden: cmd.hidden,
                description: (cmd.description || '').trim(),
                examples: [...(cmd.examples || [])],
                aliases: [...(cmd.aliases || [])],
                sub_commands: [...(cmd.sub_commands || [])],
            };
            if (cmd.type === 'command') {
                return {...base, type: 'command', command: cmd.command.trim()};
            }
            return {...base, type: 'regex', pattern: cmd.pattern};
        });
        const data = {
            group_name: this.form.groupName.trim(),
            description: this.form.groupDesc.trim(),
            priority: this.form.priority || 0,
            hidden: this.form.hidden || false,
            commands: cmdList,
        };
        try {
            if (this.editingIndex >= 0) {
                await updateCustomGroup(
                    this.editingIndex,
                    this.form.currentGroupName,
                    data,
                );
            } else {
                await createCustomGroup(data);
            }
            this.showToast(this.editingIndex >= 0 ? '分组已更新' : '分组已创建');
            this._clearDraft();
            this.resetForm();
            this.closePanel();
            await this.loadGroups();
        } catch (err) {
            this.showToast('保存失败: ' + err.message, 'error');
        }
    },

    async handleDelete() {
        if (this.editingIndex < 0) return;
        const groupName = this.groups[this.editingIndex].group_name;
        let preview;
        try {
            preview = await previewDeleteCustomGroup(groupName);
        } catch (err) {
            this.showToast('删除预览失败: ' + err.message, 'error');
            return;
        }
        const summary = preview.group || {};
        const ok = await this.showConfirm(
            `将删除“${summary.group_name || groupName}”及 ${(summary.commands || []).length} 条目录命令，此操作不可恢复。`,
            '确认删除分组', '删除', 'btn-danger'
        );
        if (!ok) return;
        try {
            await deleteCustomGroup(groupName, preview.delete_token);
            this.showToast('分组已删除');
            this._clearDraft();
            this.resetForm();
            this.closePanel();
            await this.loadGroups();
        } catch (err) {
            this.showToast('删除失败: ' + err.message, 'error');
        }
    },

    async loadCatalog(page = 1) {
        try {
            this.catalog = await getCommands({
                page,
                pageSize: this.catalog.page_size,
                query: this.catalogQuery,
            });
        } catch (err) {
            this.showToast('加载命令目录失败: ' + err.message, 'error');
        }
    },

    async saveCatalogPolicy(item) {
        try {
            if (item.permission_level === 'admin' && item.delegation_policy === 'normal') {
                item.delegation_policy = 'sensitive';
            }
            await updateCommandPolicy(item.id, {
                permission_level: item.permission_level,
                delegation_policy: item.delegation_policy,
                history_mode: item.history_mode,
            });
            this.showToast('命令策略已更新');
            await this.loadGroups();
        } catch (err) {
            this.showToast('策略更新失败: ' + err.message, 'error');
            await this.loadCatalog(this.catalog.page);
        }
    },
});

// Expose to window for debugging
window.store = store;

// Init
initTheme();
ready()
    .then(() => Promise.all([store.loadGroups(), store.loadCatalog()]))
    .catch((err) => store.showToast('初始化失败: ' + err.message, 'error'));

// Mount petite-vue - use store as the root scope data
PetiteVue.createApp(store).mount('.container');
window.__vueMounted = true;
