import React from 'react';
import { useUser } from '../context/UserContext';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert } from 'lucide-react';

/**
 * Wrap any page that requires an active user session.
 * Redirects to /login if no user is set.
 */
export default function RequireUser({ children }) {
  const { userId } = useUser();
  const navigate = useNavigate();

  if (!userId) {
    return (
      <div className="empty-state">
        <ShieldAlert size={48} />
        <h2>No active profile</h2>
        <p>Sign in first to use this feature.</p>
        <button className="btn btn-primary" onClick={() => navigate('/login')}>
          Go to Login
        </button>
      </div>
    );
  }

  return children;
}
