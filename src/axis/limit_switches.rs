#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LimitSwitches {
    None,
    Upper,
    Lower,
    Both,
}

impl LimitSwitches {
    pub fn has_upper(&self) -> bool {
        matches!(self, LimitSwitches::Upper | LimitSwitches::Both)
    }

    pub fn has_lower(&self) -> bool {
        matches!(self, LimitSwitches::Lower | LimitSwitches::Both)
    }

    pub fn is_clear(&self) -> bool {
        matches!(self, LimitSwitches::None)
    }

    pub fn any_active(&self) -> bool {
        !self.is_clear()
    }
}
