import { create } from 'zustand'
import { api } from '../lib/api'
import type { User } from '../lib/types'

interface AuthState {
  user: User | null
  loading: boolean
  initialized: boolean
  fetchMe: () => Promise<void>
  login: (username: string, password: string) => Promise<User>
  register: (payload: {
    username: string
    password: string
    confirm: string
    email: string
  }) => Promise<void>
  logout: () => Promise<void>
  patch: (partial: Partial<User>) => void
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: false,
  initialized: false,

  fetchMe: async () => {
    set({ loading: true })
    try {
      const res = await api.get<{ user: User | null }>('/auth/me')
      set({ user: res.user ?? null, initialized: true })
    } catch {
      set({ user: null, initialized: true })
    } finally {
      set({ loading: false })
    }
  },

  login: async (username, password) => {
    const res = await api.post<{ user: User }>('/auth/login', { username, password })
    set({ user: res.user, initialized: true })
    return res.user
  },

  register: async (payload) => {
    await api.post('/auth/register', payload)
  },

  logout: async () => {
    try {
      await api.post('/auth/logout')
    } finally {
      set({ user: null })
    }
  },

  patch: (partial) =>
    set((state) => ({ user: state.user ? { ...state.user, ...partial } : null })),
}))
