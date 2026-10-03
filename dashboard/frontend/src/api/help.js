/**
 * The Help panel's conversation (dashboard/backend/routes/help_chat.py): the
 * support agent, one thread per user, so none of these take a key. The turn
 * itself streams through `streamEntityChat` with `helpChatUrl()` and the page
 * the user is on as its extra body.
 */
import api from './index';

export const getHelpChat = () => api.get('/help-chat');
export const clearHelpChat = () => api.delete('/help-chat');
export const stopHelpChat = () => api.post('/help-chat/stop');
export const helpChatUrl = () => '/help-chat';
