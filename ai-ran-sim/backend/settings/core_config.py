from .slice_config import (
    NETWORK_SLICE_EMBB_NAME,
    NETWORK_SLICE_URLLC_NAME,
    NETWORK_SLICE_MTC_NAME,
)
from .ue_config import UE_DEFAULT_MAX_COUNT

import random

CORE_UE_SUBSCRIPTION_DATA = {}

for i in range(UE_DEFAULT_MAX_COUNT):
    UE_IMSI = f"IMSI_{i}"

    # randomly pick the slice subscriptions for UEs
    # first roughly 20% of UE is an IoT device, meaning that it is subscribed to mMTC slice
    if random.random() < 0.2:
        CORE_UE_SUBSCRIPTION_DATA[UE_IMSI] = [NETWORK_SLICE_MTC_NAME]
        continue

    # for all general UEs, they are by defautl subscribed to eMBB slice
    CORE_UE_SUBSCRIPTION_DATA[UE_IMSI] = [NETWORK_SLICE_EMBB_NAME]

    # then 50% chance the UE also subscribes to URLLC slice
    if random.random() < 0.5:
        CORE_UE_SUBSCRIPTION_DATA[UE_IMSI].append(NETWORK_SLICE_URLLC_NAME)
