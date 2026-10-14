"""Battery sensor for WeBack robot vacuums."""

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE

from . import DOMAIN, VacDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Weback battery sensors."""
    sensors = [WebackBatterySensor(device) for device in hass.data[DOMAIN]]
    _LOGGER.debug("Adding Weback battery sensors to Home Assistant: %s", sensors)
    async_add_entities(sensors, False)


class WebackBatterySensor(SensorEntity):
    """
    Weback vacuum battery level
    (vacuum entities no longer carry the battery since HA 2026)
    """

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(self, device: VacDevice):
        """Initialize the battery sensor."""
        self.device = device

    async def async_added_to_hass(self):
        """Subscribe to cloud updates once hass is attached to the entity."""
        self.device.subscribe(lambda vacdevice: self.schedule_update_ha_state(False))

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self.device.nickname} Battery"

    @property
    def unique_id(self) -> str:
        """Return an unique ID."""
        return f"{self.device.name}_battery"

    @property
    def available(self):
        """Returns true if vacuum is online"""
        return self.device.is_available

    @property
    def native_value(self):
        """Return the battery level."""
        return self.device.battery_level
