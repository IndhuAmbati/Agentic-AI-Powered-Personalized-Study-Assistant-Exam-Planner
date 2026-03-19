import React, { createContext, useContext, useState, useEffect } from 'react';

const UserContext = createContext(null);

export function UserProvider({ children }) {
  const [userId, setUserId] = useState(() => localStorage.getItem('userId') || '');
  const [userName, setUserName] = useState(() => localStorage.getItem('userName') || '');

  useEffect(() => {
    localStorage.setItem('userId', userId);
    localStorage.setItem('userName', userName);
  }, [userId, userName]);

  const login = (id, name) => {
    setUserId(id);
    setUserName(name);
  };

  const logout = () => {
    setUserId('');
    setUserName('');
  };

  return (
    <UserContext.Provider value={{ userId, userName, login, logout }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser() {
  const ctx = useContext(UserContext);
  if (!ctx) throw new Error('useUser must be used within UserProvider');
  return ctx;
}
