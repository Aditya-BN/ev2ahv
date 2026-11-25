# ev2ahv UI vs Nutanix Move 6.2, side by side

Left: Move (read-only capture, docs/move-reference). Right: ev2ahv in mock mode. Layout, flow and wording follow Move; components, CSS and brand are ev2ahv's own (no Move assets).
Wizard steps 4-6 of Move are reconstructed, not observed (Move failed to create a plan: vCenter DNS).
ev2ahv-only screens with no Move pair: wizard step 5 VM settings, step 6 schedule, cutover blocked by prep-check, rollback, per-VM engine log (docs/ui-shots).

## Dashboard

![Dashboard](side-by-side/sbs-01-dashboard.png)

## Events

![Events](side-by-side/sbs-05-events.png)

## User menu

![User menu](side-by-side/sbs-06-user-menu.png)

## Settings menu

![Settings menu](side-by-side/sbs-07-settings-menu.png)

## About

![About](side-by-side/sbs-24-about.png)

## Change password

![Change password](side-by-side/sbs-25-change-password.png)

## Environment card menu

![Environment card menu](side-by-side/sbs-26-environment-card-menu.png)

## Add environment

![Add environment](side-by-side/sbs-28-add-environment.png)

## Plan row actions

![Plan row actions](side-by-side/sbs-30-plan-row-actions.png)

## Plan summary

![Plan summary](side-by-side/sbs-39-plan-summary.png)

## Migration progress

![Migration progress](side-by-side/sbs-40-plan-details.png)

## Completed, target VM link

![Completed, target VM link](side-by-side/sbs-44-plan-completed.png)

## Completed VMs list

![Completed VMs list](side-by-side/sbs-45-completed-vms.png)

## Wizard: name

![Wizard: name](side-by-side/sbs-82-wizard-name-gate.png)

## Wizard 1: source and target

![Wizard 1: source and target](side-by-side/sbs-93-step1-container.png)

## Wizard 2: select VMs

![Wizard 2: select VMs](side-by-side/sbs-94-step2-select-vms.png)

## Wizard 2: warnings

![Wizard 2: warnings](side-by-side/sbs-101-step2-warnings.png)

## Wizard 3: network

![Wizard 3: network](side-by-side/sbs-114-step3-mapped.png)

## Wizard 4: VM preparation (Move side: Move stopped at its step 3 capacity check, so its step 4 was never observed)

![Wizard 4: VM preparation](side-by-side/sbs-115-step4-prep.png)

## Leave wizard

![Leave wizard](side-by-side/sbs-119-wizard-leave-confirm.png)

## Bandwidth throttling

![Bandwidth throttling](side-by-side/sbs-09-bandwidth-empty.png)

## Realtime logs

![Realtime logs](side-by-side/sbs-12-realtime-logs.png)

