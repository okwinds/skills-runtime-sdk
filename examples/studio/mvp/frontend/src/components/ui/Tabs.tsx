import React, { createContext, useContext, useState, useCallback } from 'react';
import './Tabs.css';

interface TabsContextValue {
  activeTab: string;
  setActiveTab: (id: string) => void;
}

const TabsContext = createContext<TabsContextValue | undefined>(undefined);

const useTabs = (): TabsContextValue => {
  const context = useContext(TabsContext);
  if (!context) {
    throw new Error('Tabs components must be used within a Tabs provider');
  }
  return context;
};

// Tabs Container
interface TabsProps {
  children: React.ReactNode;
  defaultTab: string;
  activeTab?: string;
  onChange?: (tabId: string) => void;
}

export const Tabs: React.FC<TabsProps> & {
  List: typeof TabsList;
  Tab: typeof Tab;
  Content: typeof TabsContent;
  Panel: typeof TabPanel;
} = ({ children, defaultTab, activeTab: controlledActiveTab, onChange }) => {
  const [uncontrolledActiveTab, setUncontrolledActiveTab] = useState(defaultTab);

  const setActiveTab = useCallback(
    (id: string) => {
      if (controlledActiveTab === undefined) {
        setUncontrolledActiveTab(id);
      }
      onChange?.(id);
    },
    [controlledActiveTab, onChange]
  );

  return (
    <TabsContext.Provider value={{ activeTab: controlledActiveTab ?? uncontrolledActiveTab, setActiveTab }}>
      <div className="tabs" role="tablist">{children}</div>
    </TabsContext.Provider>
  );
};

// Tabs List
interface TabsListProps {
  children: React.ReactNode;
}

const TabsList: React.FC<TabsListProps> = ({ children }) => {
  return <div className="tabs__list">{children}</div>;
};

// Individual Tab
interface TabProps {
  id: string;
  children: React.ReactNode;
  icon?: React.ReactNode;
  badge?: number | string;
  disabled?: boolean;
}

const Tab: React.FC<TabProps> = ({ id, children, icon, badge, disabled }) => {
  const { activeTab, setActiveTab } = useTabs();
  const isActive = activeTab === id;

  return (
    <button
      role="tab"
      aria-selected={isActive}
      aria-controls={`tabpanel-${id}`}
      id={`tab-${id}`}
      tabIndex={isActive ? 0 : -1}
      className="tabs__tab"
      onClick={() => setActiveTab(id)}
      disabled={disabled}
    >
      {icon && <span className="tabs__icon">{icon}</span>}
      {children}
      {badge !== undefined && <span className="tabs__badge">{badge}</span>}
    </button>
  );
};

// Tabs Content Container
interface TabsContentProps {
  children: React.ReactNode;
}

const TabsContent: React.FC<TabsContentProps> = ({ children }) => {
  return <div className="tabs__content">{children}</div>;
};

// Tab Panel
interface TabPanelProps {
  id: string;
  children: React.ReactNode;
}

const TabPanel: React.FC<TabPanelProps> = ({ id, children }) => {
  const { activeTab } = useTabs();
  const isActive = activeTab === id;

  return (
    <div
      role="tabpanel"
      id={`tabpanel-${id}`}
      aria-labelledby={`tab-${id}`}
      hidden={!isActive}
      className="tabs__panel"
      tabIndex={0}
    >
      {children}
    </div>
  );
};

// Attach subcomponents
Tabs.List = TabsList;
Tabs.Tab = Tab;
Tabs.Content = TabsContent;
Tabs.Panel = TabPanel;
