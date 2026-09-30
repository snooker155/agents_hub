// Shapes the page resets to, shared by the tabs that write them.

export const EMPTY_MODEL = { provider: 'inherit', model: '', api_key: '', base_url: '', temperature: '', max_tokens: '' };

export const PLANNING_TOOLS = ['save_plan', 'get_plan', 'list_plans', 'update_plan_status', 'delete_plan'];

export const defaultReasoningSettings = { thinkEnabled: false, thinkMode: 'standard', thinkingLevel: 'off', planEnabled: false, planFormat: 'structured' };
