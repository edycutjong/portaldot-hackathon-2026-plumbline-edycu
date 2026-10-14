# Example chain guide (hand-written test fixture)

## 1. Basics

### 1.1 Wallet Setup

| Call | Description |
|---|---|
| `orphan` | a table under a heading that is not a pallet name |

## 2. Modules

### 2.1 Tokens

#### 2.1.1 Sample Code

| Call | Description |
|---|---|
| `issue` | one name |
| `lock` / `unlock` | two names in one cell |
| plain words | no backticks, so no call |

Use `issue` in prose: prose is not a call table.
Go to **Network → Tokens** (a UI page, not a call).

### 2.2 Gadgets

| Call | Description |
|---|---|
| `make_gadget` | under the next pallet heading |

1. Query **Developer → Runtime calls → gadgetApi → lookup** first.
2. Then send **Developer → Extrinsics → Gadgets → make_gadget**.

### 2.3 Widgets

```text
### Setup
| Call | Description |
| `not_a_call` | inside a code fence |
```

  | Function | Description |
  |---|---|
  | `spin` | an indented table with a Function header |
  | spin_fast | an unticked name |
  | `lock_it` (needs `CollectionConfig`) | extra ticks in the cell |
  | `wobble` | see **Developer** → **Extrinsics** → **Widgets** → **wobble** inside a row |

> | Call | Description |
> |---|---|
> | `quoted` | a quoted table |

Then use Developer -> Extrinsics -> Widgets -> transferKeepAlive (ASCII arrows, camelCase name).
