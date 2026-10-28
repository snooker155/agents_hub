import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './auth';
import { isMenuHidden } from './navMenu';

/**
 * A page an administrator hid from members (`hidden_menu` on GET /auth/me)
 * sends this viewer to the chat, as the /users guard does for its page. A
 * path matches its own row and everything under it. Not access control: the
 * server refuses what members may not change.
 */
export default function HiddenMenuGuard({ children }) {
  const { user } = useAuth();
  const { pathname } = useLocation();
  if (isMenuHidden(user?.hidden_menu, pathname)) return <Navigate to="/chat" replace />;
  return children;
}
