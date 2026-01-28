import log

import utime
from misc import ADC
import ujson
import _thread
import modem
from machine import I2C
import fota
import app_fota
from misc import Power
from machine import UART, ExtInt, Pin
from queue import Queue
from umqtt import MQTTClient
import pm
from machine import Pin
import sim
from machine import RTC
import uos
from machine import UART
import gc
import net
import checkNet
from machine import ExtInt
import uhashlib
import ubinascii
import ustruct
import sys
from machine import Timer

# ===== FROM FILE: battery.py =====
logger_battery = log.getLogger("WGPS::Battery")


class Battery:

    def __init__(self, adc_period, channel, factor, min_voltage, max_voltage):
        self.adc_period = adc_period
        self.channel = channel
        self.factor = factor
        self.min_voltage = min_voltage
        self.max_voltage = max_voltage
        self.adc = ADC()

    def get_adc_value(self, adc, adc_period, adc_num, factor):
        adc.open()
        utime.sleep_ms(adc_period)
        adc_list = list()
        for i in range(20):
            adc_list.append(adc.read(adc_num))
            utime.sleep_ms(adc_period)
        adc_list.remove(min(adc_list))
        adc_list.remove(max(adc_list))
        if len(adc_list) == 0:
            adc.close()
            return -1
        adc_value = int(sum(adc_list) / len(adc_list))
        adc.close()
        adc_value = adc_value * factor
        return adc_value

    def get_battery_level(self):
        try:
            batt_v = self.get_adc_value(self.adc, self.adc_period, self.channel, self.factor) / 1000.0
            logger_battery.info("Raw battery voltage reading: {} V".format(batt_v))
            if batt_v < 0:
                logger_battery.error("Failed to read ADC value")
                return (None, None)
            batt_level = (batt_v - self.min_voltage) / (self.max_voltage - self.min_voltage) * 100.0
            battery_level = round(max(0, min(100, batt_level)), 3)
            logger_battery.info("Battery level: {:.3f}%".format(battery_level))
            return (battery_level, batt_v)
        except Exception as e:
            logger_battery.error("Error getting battery level: {}".format(e))
            return (None, None)


# ===== FROM FILE: configs.py =====
logger_configs = log.getLogger("WGPS::Configs")


class WGPS_Configs:

    def __init__(self, config_file="/usr/config.json"):
        self.config_file = config_file
        self.configs = {}
        try:
            self._lock = _thread.allocate_lock()
        except Exception:
            self._lock = None

    def _recursive_merge(self, base_cfg, new_cfg):
        for key, value in new_cfg.items():
            if key in base_cfg and isinstance(base_cfg[key], dict) and isinstance(value, dict):
                self._recursive_merge(base_cfg[key], value)
            else:
                base_cfg[key] = value

    def load_configs(self):
        try:
            logger_configs.info("Loading configurations from config JSON: %s...", self.config_file)
            if self._lock:
                self._lock.acquire()
            try:
                with open(self.config_file, "r") as f:
                    data = ujson.load(f)
                    self.configs = {}
                    for name, cfg in data.items():
                        self.configs[name] = cfg
            finally:
                if self._lock:
                    self._lock.release()
            logger_configs.info("Loaded config JSON file: %s", self.config_file)
            return True
        except Exception as e:
            logger_configs.error("Failed to load config JSON file '%s': %s", self.config_file, e)
            return False

    def get_config(self, key_path):
        if self._lock:
            self._lock.acquire()
        try:
            keys = key_path.split(".")
            if not keys or keys[0] not in self.configs:
                logger_configs.error("Config '{}' not found!".format(keys[0] if keys else ""))
                return None
            cfg = self.configs[keys[0]]
            for key in keys[1:]:
                if isinstance(cfg, dict) and key in cfg:
                    cfg = cfg[key]
                else:
                    logger_configs.error("Key path '{}' not found in config '{}'.".format(key_path, keys[0]))
                    return None
            return cfg
        finally:
            if self._lock:
                self._lock.release()

    def save_all_configs(self, use_lock=True):
        if not self.config_file:
            logger_configs.error("No config_file specified; cannot save all configs")
            return False
        try:
            if self._lock and use_lock:
                self._lock.acquire()
            try:
                with open(self.config_file, "w") as f:
                    ujson.dump(self.configs, f)
            finally:
                if self._lock and use_lock:
                    self._lock.release()
            logger_configs.info("Saved all configs to config JSON: %s", self.config_file)
            return True
        except Exception as e:
            logger_configs.error("Failed to save all configs to config JSON: %s", e)
            return False

    def set_config(self, key_path, value, save=True):
        if self._lock:
            self._lock.acquire()
        try:
            keys = key_path.split(".")
            if not keys or keys[0] not in self.configs:
                logger_configs.error("Config '{}' not found! Cannot set value.".format(keys[0] if keys else ""))
                return False
            cfg = self.configs[keys[0]]
            for key in keys[1:-1]:
                if key not in cfg or not isinstance(cfg[key], dict):
                    cfg[key] = {}
                cfg = cfg[key]
            cfg[keys[-1]] = value
            if save:
                return self.save_all_configs(use_lock=False)
            return True
        finally:
            if self._lock:
                self._lock.release()

    def merge_configs(self, new_configs, save=True):
        backup = {}
        if self._lock:
            self._lock.acquire()
        try:
            with open(self.config_file, "r") as f:
                backup = ujson.load(f)
            if not isinstance(new_configs, dict):
                logger_configs.error("New configs must be a dictionary.")
                return False
            for name, cfg in new_configs.items():
                if name not in self.configs:
                    self.configs[name] = cfg
                else:
                    self._recursive_merge(self.configs[name], cfg)
            if save:
                return self.save_all_configs(use_lock=False)
            return True
        except Exception as e:
            logger_configs.error("Failed to merge configs: {}".format(e))
            try:
                self.configs = backup
                self.save_all_configs(use_lock=False)
            except Exception:
                logger_configs.error("Failed to restore backup configs after merge failure")
            return False
        finally:
            if self._lock:
                self._lock.release()

    def create_backup(self, backup_file="/usr/config_backup.json"):
        try:
            if self._lock:
                self._lock.acquire()
            try:
                with open(backup_file, "w") as f:
                    ujson.dump(self.configs, f)
            finally:
                if self._lock:
                    self._lock.release()
            logger_configs.info("Created backup of configs at: %s", backup_file)
            return True
        except Exception as e:
            logger_configs.error("Failed to create backup of configs: %s", e)
            return False

    def restore_backup(self, backup_file="/usr/config_backup.json"):
        try:
            logger_configs.info("Restoring configs from backup file: %s", backup_file)
            if self._lock:
                self._lock.acquire()
            try:
                with open(backup_file, "r") as f:
                    backup_configs = ujson.load(f)
                    self.configs = backup_configs
                    self.save_all_configs(use_lock=False)
            finally:
                if self._lock:
                    self._lock.release()
            logger_configs.info("Restored configs from backup successfully.")
            return True
        except Exception as e:
            logger_configs.error("Failed to restore configs from backup: %s", e)
            return False


wgps_configs = WGPS_Configs()

# ===== FROM FILE: device_info.py =====
logger_device_info = log.getLogger("WGPS::DeviceInfo")
project_name = "WGPS"
fw_version = "0.0.0"
startup_time = (0, 0, 0, 0, 0, 0)
startup_time_str = "2025-01-01T00:00:00+05:30"


def get_device_imei():
    return modem.getDevImei()


def set_project_name(name):
    global project_name
    project_name = name


def get_project_name():
    global project_name
    return project_name


def set_firmware_version(version):
    global fw_version
    fw_version = version


def get_firmware_version():
    global fw_version
    return fw_version


def set_startup_time(time_str, timestamp):
    global startup_time, startup_time_str
    startup_time_str = time_str
    startup_time = timestamp


def get_startup_time():
    global startup_time
    return startup_time


def get_startup_time_str():
    global startup_time_str
    return startup_time_str


# ===== FROM FILE: ec200u_i2c.py =====
logger_ec200u_i2c = log.getLogger("WGPS::EC200U_I2C")
i2c_devices = {}
i2c_locks = {}


class EC200U_I2C:

    def __init__(self, i2c_channel, address):
        try:
            global i2c_devices, i2c_locks
            self.i2c_channel = i2c_channel
            self.address = address
            if i2c_channel not in list(i2c_devices.keys()):
                i2c_devices[i2c_channel] = I2C(i2c_channel, I2C.STANDARD_MODE)
                i2c_locks[i2c_channel] = _thread.allocate_lock()
                self.i2c = i2c_devices[i2c_channel]
                self.lock = i2c_locks[i2c_channel]
            else:
                self.i2c = i2c_devices[i2c_channel]
                self.lock = i2c_locks[i2c_channel]
        except Exception as e:
            raise RuntimeError("EC200U I2C init failed: {}".format(e))

    def read_register(self, reg_addr, length=1):
        try:
            r_data = bytearray(length)
            regaddr = bytearray([reg_addr])
            while self.lock.locked():
                utime.sleep_ms(50)
            self.lock.acquire()
            self.i2c.read(self.address, regaddr, len(regaddr), r_data, length, 0)
            self.lock.release()
            return r_data
        except Exception as e:
            raise RuntimeError("EC200U I2C read failed: {}".format(e))

    def write_register(self, reg_addr, data):
        try:
            regaddr = bytearray([reg_addr])
            while self.lock.locked():
                utime.sleep_ms(50)
            self.lock.acquire()
            self.i2c.write(self.address, regaddr, len(regaddr), data, len(data))
            self.lock.release()
        except Exception as e:
            raise RuntimeError("EC200U I2C write failed: {}".format(e))


# ===== FROM FILE: fota_manager.py =====
logger_fota_manager = log.getLogger("FotaManager")


class FotaManager:

    def __init__(self, app_name, app_version, dev_id, dev_imei):
        self.app_name = app_name
        self.app_version = app_version
        self.dev_id = dev_id
        self.dev_imei = dev_imei
        logger_fota_manager.info("FotaManager initialized for app: {}, version: {}, device_id: {}, device_imei: {}".format(app_name, app_version, dev_id, dev_imei))
        self.fota_instance = None
        self.fota_type = None

    def start_user_fota(self, payload):
        logger_fota_manager.info("Performing user fota for : {}".format(ujson.dumps(payload)))
        try:
            if self.fota_instance is not None:
                raise Exception("FOTA instance already initialized.")
            self.fota_instance = app_fota.new()
            self.fota_type = "user"
            failed = self.fota_instance.bulk_download(payload)
            if len(failed) > 0:
                raise Exception("Some files failed to download: {}".format(failed))
        except Exception as e:
            raise Exception("User FOTA failed: {}".format(e))

    def start_fota_update(self, server_url):
        logger_fota_manager.info("Starting FOTA update from URL: {}".format(server_url))
        try:
            if self.fota_instance is not None:
                raise Exception("FOTA instance already initialized.")
            self.fota_instance = fota(reset_disable=1)
            self.fota_type = "system"
            if self.fota_instance.httpDownload(url1=server_url, callback=self.download_callback) != 0:
                raise Exception("FOTA download failed")
            logger_fota_manager.info("FOTA download started successfully...")
        except Exception as e:
            raise Exception("FOTA update failed: {}".format(e))

    def complete_fota(self):
        logger_fota_manager.info("Completing update...")
        try:
            if self.fota_instance is None:
                raise Exception("FOTA instance is not initialized.")
            if self.fota_type == "user":
                self.fota_instance.set_update_flag()
                Power.powerRestart()
            elif self.fota_type == "system":
                Power.powerRestart()
            else:
                raise Exception("Unknown FOTA type: {}".format(self.fota_type))
        except Exception as e:
            raise Exception("Completing User FOTA failed: {}".format(e))

    def download_callback(self, args):
        logger_fota_manager.info("FOTA Download Status: {}, Progress: {}%".format(args[0], args[1]))

    def parse_fota_request(self, req_msg):
        fota_req = ujson.loads(req_msg)
        logger_fota_manager.info("Parsed FOTA request: {}".format(fota_req))
        return fota_req

    def version_tuple(self, version_str):
        return tuple(map(int, version_str.split(".")))

    def verify_version_increment(self, new_version):
        current_version_tuple = self.version_tuple(self.app_version)
        new_version_tuple = self.version_tuple(new_version)
        if new_version_tuple >= current_version_tuple:
            logger_fota_manager.info("New version {} is greater than or equal to current version {}".format(new_version, self.app_version))
            return True
        else:
            logger_fota_manager.error("New version {} is not greater than current version {}".format(new_version, self.app_version))
            return False

    def verify_fota_request(self, fota_req):
        required_keys = ["device_id", "device_imei", "app", "version", "type"]
        for key in required_keys:
            if key not in fota_req:
                logger_fota_manager.error("Missing key in FOTA request: {}".format(key))
                return False
        if fota_req["device_id"] != self.dev_id or fota_req["device_imei"] != self.dev_imei or fota_req["app"] != self.app_name or (not self.verify_version_increment(fota_req["version"])):
            logger_fota_manager.error("FOTA request verification failed.")
            return False
        if fota_req["type"] == "system":
            if "url" not in fota_req:
                logger_fota_manager.error("Missing URL for system FOTA request.")
                return False
        elif fota_req["type"] == "user":
            if "download_list" not in fota_req:
                logger_fota_manager.error("Missing params for user FOTA request.")
                return False
            for download_item in fota_req["download_list"]:
                if "url" not in download_item or "file_name" not in download_item:
                    logger_fota_manager.error("Invalid download item in user FOTA request: {}".format(download_item))
                    return False
        logger_fota_manager.info("FOTA request verified successfully.")
        return True

    def handle_fota_request(self, fota_req):
        try:
            if fota_req["type"] == "user":
                self.start_user_fota(fota_req["download_list"])
            elif fota_req["type"] == "system":
                self.start_fota_update(fota_req["url"])
        except Exception as e:
            raise Exception("handle_fota_request: {}".format(e))


# ===== FROM FILE: generic_data.py =====
class GenericData:

    def __init__(self):
        self.data = {}

    def get(self, key_path):
        keys = key_path.split(".")
        value = self.data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value

    def set(self, key_path, value):
        keys = key_path.split(".")
        d = self.data
        for key in keys[:-1]:
            if key not in d or not isinstance(d[key], dict):
                d[key] = {}
            d = d[key]
        d[keys[-1]] = value

    def get_data(self):
        return self.data

    def set_data(self, data):
        self.data = data


# ===== FROM FILE: interrupt_handler.py =====
logger_interrupt_handler = log.getLogger("WGPS::InterruptHandler")


class InterruptHandler:

    def __init__(self, interrupt_keys: list):
        self.interrupt_keys = interrupt_keys
        self.interrupts_enabled = False
        self.wakeup_interrupts = {}
        self.normal_interrupts = {}
        for key in self.interrupt_keys:
            self.wakeup_interrupts[key] = []
            self.normal_interrupts[key] = []

    def register_interrupt(self, name, wakeup_cb, wakeup_cb_args, intr_cb, intr_cb_args):
        if name not in self.interrupt_keys:
            logger_interrupt_handler.error("Invalid interrupt name: {}".format(name))
            return
        if wakeup_cb is not None:
            self.wakeup_interrupts[name].append((wakeup_cb, wakeup_cb_args))
        if intr_cb is not None:
            self.normal_interrupts[name].append((intr_cb, intr_cb_args))

    def enable_interrupts(self):
        self.interrupts_enabled = True

    def disable_interrupts(self):
        self.interrupts_enabled = False

    def handle_interrupt(self, name, is_wakeup):
        if not self.interrupts_enabled:
            return
        if name not in self.interrupt_keys:
            logger_interrupt_handler.error("Invalid interrupt name: {}".format(name))
            return
        try:
            callbacks = self.wakeup_interrupts[name] if is_wakeup else self.normal_interrupts[name]
            for cb_tuple in callbacks:
                cb, cb_args = cb_tuple
                cb(*cb_args)
        except Exception as e:
            logger_interrupt_handler.error("Error handling interrupt {}: {}".format(name, e))


# ===== FROM FILE: l89.py =====
logger_l89 = log.getLogger("WGPS::L89")


class L89:

    def __init__(self, uart_num, fix3d_pin, enable_pin, reset_pin, wakeup_pin, baud=9600):
        self.uart_num = uart_num
        self.fix3d_pin = fix3d_pin
        self.baud = baud
        self.extint = ExtInt(self.fix3d_pin, ExtInt.IRQ_RISING, ExtInt.PULL_DISABLE, self.fix_3d_callback)
        self.gnss_en = Pin(enable_pin, Pin.OUT, Pin.PULL_DISABLE, 0)
        self.gnss_reset = Pin(reset_pin, Pin.OUT, Pin.PULL_DISABLE, 1)
        self.gnss_wakeup = Pin(wakeup_pin, Pin.OUT, Pin.PULL_DISABLE, 0)
        self.uart = UART(self.uart_num, self.baud, 8, 0, 1, 0)
        self.ready = False
        self.thread = None
        self.keep_running = False
        self.thread_joined = False
        self.queue = Queue(0)
        self.data = {"dtpf": "", "fix": "", "lat": "", "latd": "", "lng": "", "lngd": "", "alt": "", "nos": 0, "spd": "", "navst": 0, "sv": "", "hdop": 0.0, "pdop": 0.0}
        self.req_params = list(self.data.keys())

    def calc_checksum(self, data: bytes) -> int:
        if not data or len(data) < 1:
            return 0
        result = 0
        for b in data:
            result ^= b
        return result

    def wakeup(self):
        self.gnss_en.write(1)

    def sleep(self):
        self.uart.write(b"$PAIR650,0\r\n")
        utime.sleep(1)
        self.gnss_en.write(0)

    def start(self):
        if self.thread is not None:
            logger_l89.warning("L89 GPS module thread already running")
            return
        try:
            self.keep_running = True
            self.thread_joined = False
            self.thread = _thread.start_new_thread(self.thread_func, ())
            logger_l89.info("L89 GPS module initialized successfully.")
        except Exception as e:
            logger_l89.error("Failed to initialize L89 GPS module: {}".format(e))

    def stop(self):
        if self.thread is not None:
            self.keep_running = False
            while not self.thread_joined:
                utime.sleep(1)
            self.thread = None
        logger_l89.info("L89 GPS module stopped.")

    def get_data(self):
        return self.data

    def is_ready(self):
        return self.ready

    def thread_func(self):
        try:
            self.wakeup()
            buffer = b""
            while self.keep_running and (not self.ready):
                data_len = self.uart.any()
                if data_len > 0:
                    buffer += self.uart.read(data_len)
                    if b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.decode("utf-8").strip()
                        self.extract_data(line)
                utime.sleep_ms(100)
            self.extint.disable()
            self.sleep()
        except Exception as e:
            logger_l89.error("Error in GPS thread: {}".format(e))
        self.thread_joined = True

    def fix_3d_callback(self, gpio_num, edge):
        if gpio_num == self.fix3d_pin and edge == ExtInt.IRQ_RISING:
            self.queue.put(True)
            logger_l89.info("3D fix acquired.")

    def remove_req_param(self, param):
        if param in self.req_params:
            self.req_params.remove(param)
            logger_l89.debug("Removed param {} from req_params".format(param))
            logger_l89.debug("Remaining req_params: {}".format(self.req_params))

    def extract_data(self, line):
        if line.startswith("$"):
            parts = line.split(",")
            if len(parts) < 1:
                return
            nmea_id = parts[0][3:6]
            if nmea_id == "RMC":
                self.extract_rmc(parts)
            elif nmea_id == "VTG":
                self.extract_vtg(parts)
            elif nmea_id == "GGA":
                self.extract_gga(parts)
            elif nmea_id == "GSA":
                self.extract_gsa(parts)
            else:
                logger_l89.debug("Unhandled NMEA ID: {}".format(nmea_id))
            if len(self.req_params) == 0:
                logger_l89.info("All GNSS parameters extracted.")
                self.ready = True

    def extract_rmc(self, parts):
        try:
            if len(parts) < 13 or parts[2] != "A":
                return
            if "dtpf" in self.req_params:
                time_str = parts[1]
                date_str = parts[9]
                if time_str and date_str:
                    try:
                        hh = int(time_str[0:2])
                        mm = int(time_str[2:4])
                        ss = int(time_str[4:6])
                        dd = int(date_str[0:2])
                        mo = int(date_str[2:4])
                        yy = int(date_str[4:6])
                        year = 2000 + yy if yy < 80 else 1900 + yy
                        utc_tuple = (year, mo, dd, hh, mm, ss, 0, 0)
                        utc_secs = utime.mktime(utc_tuple)
                        ist_secs = utc_secs + 19800
                        ist_tuple = utime.localtime(ist_secs)
                        dtpf = "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}+05:30".format(ist_tuple[0], ist_tuple[1], ist_tuple[2], ist_tuple[3], ist_tuple[4], ist_tuple[5])
                        self.data["dtpf"] = dtpf
                        self.data["dtpf_set"] = utime.time()
                        self.remove_req_param("dtpf")
                    except Exception:
                        logger_l89.error("Error parsing date/time in RMC")
            if "lat" in self.req_params or "latd" in self.req_params:
                lat_raw = parts[3]
                lat_dir = parts[4]
                if lat_raw and lat_dir:
                    try:
                        deg = int(lat_raw[0:2])
                        mins = float(lat_raw[2:])
                        lat = deg + mins / 60.0
                        if "lat" in self.req_params:
                            self.data["lat"] = round(float(lat), 6)
                            self.remove_req_param("lat")
                        if "latd" in self.req_params:
                            self.data["latd"] = str(lat_dir).strip().upper()
                            self.remove_req_param("latd")
                    except Exception:
                        logger_l89.error("Error parsing latitude in RMC")
            if "lng" in self.req_params or "lngd" in self.req_params:
                lng_raw = parts[5]
                lng_dir = parts[6]
                if lng_raw and lng_dir:
                    try:
                        deg = int(lng_raw[0:3])
                        mins = float(lng_raw[3:])
                        lng = deg + mins / 60.0
                        if "lng" in self.req_params:
                            self.data["lng"] = round(float(lng), 6)
                            self.remove_req_param("lng")
                        if "lngd" in self.req_params:
                            self.data["lngd"] = str(lng_dir).strip().upper()
                            self.remove_req_param("lngd")
                    except Exception:
                        logger_l89.error("Error parsing longitude in RMC")
        except Exception as e:
            logger_l89.error("Error extracting RMC: {}".format(e))

    def extract_vtg(self, parts):
        try:
            if "spd" in self.req_params and len(parts) >= 9:
                spd_kmh = parts[7]
                if spd_kmh:
                    try:
                        self.data["spd"] = round(float(spd_kmh), 2)
                        self.remove_req_param("spd")
                    except Exception:
                        logger_l89.error("Error parsing speed in VTG")
        except Exception as e:
            logger_l89.error("Error extracting VTG: {}".format(e))

    def extract_gga(self, parts):
        try:
            if len(parts) < 15:
                return
            if "navst" in self.req_params:
                try:
                    talker_id = parts[0][1:3] if parts[0].startswith("$") else ""
                    self.data["navst"] = 1 if talker_id == "GI" else 0
                    self.remove_req_param("navst")
                except Exception:
                    logger_l89.error("Error parsing navst (TalkerID) in RMC")
            if "fix" in self.req_params:
                try:
                    quality = int(parts[6]) if parts[6] else 0
                    if quality in (1, 2, 3):
                        self.data["fix"] = "P"
                    else:
                        self.data["fix"] = ""
                    self.remove_req_param("fix")
                except Exception:
                    logger_l89.error("Error parsing fix quality in GGA")
            if "alt" in self.req_params:
                try:
                    self.data["alt"] = round(float(parts[9]), 2) if parts[9] else ""
                    self.remove_req_param("alt")
                except Exception:
                    logger_l89.error("Error parsing altitude in GGA")
            if "nos" in self.req_params:
                try:
                    self.data["nos"] = int(parts[7]) if parts[7] else 0
                    self.remove_req_param("nos")
                except Exception:
                    logger_l89.error("Error parsing number of satellites in GGA")
        except Exception as e:
            logger_l89.error("Error extracting GGA: {}".format(e))

    def extract_gsa(self, parts):
        try:
            if len(parts) < 19:
                return
            if "sv" in self.req_params:
                prns = [prn for prn in parts[3:15] if prn]
                self.data["sv"] = ",".join(prns)
                self.remove_req_param("sv")
            if "hdop" in self.req_params:
                try:
                    self.data["hdop"] = round(float(parts[16]), 2) if parts[16] else 0.0
                    self.remove_req_param("hdop")
                except Exception:
                    logger_l89.error("Error parsing HDOP in GSA")
            if "pdop" in self.req_params:
                try:
                    self.data["pdop"] = round(float(parts[15]), 2) if parts[15] else 0.0
                    self.remove_req_param("pdop")
                except Exception:
                    logger_l89.error("Error parsing PDOP in GSA")
        except Exception as e:
            logger_l89.error("Error extracting GSA: {}".format(e))


# ===== FROM FILE: manager.py =====
class Manager:

    def __init__(self):
        self.keep_running = True
        self.thread_joined = False
        self.ready = False

    def start(self):
        pass

    def stop(self):
        pass

    def is_ready(self):
        return self.ready

    def get_data(self):
        pass

    def loop(self):
        pass


# ===== FROM FILE: mqtt_conn.py =====
logger_mqtt_conn = log.getLogger("WGPS::MQTTConn")


class MQTTConnection:

    def __init__(self, client_id, broker, port, username=None, password=None, ssl_en=False, cert_path=None, key_path=None, recv_cb=None):
        self.client_id = client_id
        self.recv_cb = recv_cb
        ssl_params = {}
        if ssl_en and cert_path is not None and (key_path is not None):
            try:
                with open(cert_path, "r") as cert_file:
                    cert_data = cert_file.read()
                with open(key_path, "r") as key_file:
                    key_data = key_file.read()
                ssl_params = {"cert": cert_data, "key": key_data}
            except Exception as e:
                logger_mqtt_conn.error("Failed to read SSL cert/key files: %s", e)
                ssl_en = False
                ssl_params = {}
        if username is not None and len(username) == 0 or (password is not None and len(password) == 0):
            username = None
            password = None
        logger_mqtt_conn.info("Initializing MQTTClient with client_id=%s, broker=%s, port=%d, username=%s, ssl_en=%s", client_id, broker, port, username, ssl_en)
        self.client = MQTTClient(client_id, broker, port, username, password, keepalive=10, ssl=ssl_en, ssl_params=ssl_params, reconn=True, version=4)
        if self.recv_cb is not None:
            self.client.set_callback(self.default_recv_cb)

    def default_recv_cb(self, topic, msg):
        if self.recv_cb is not None:
            self.recv_cb(topic, msg)

    def connect(self):
        try:
            if self.client.connect() == 0:
                logger_mqtt_conn.info("Connected to MQTT broker")
                return True
            else:
                logger_mqtt_conn.error("Failed to connect to MQTT broker")
                return False
        except Exception as e:
            logger_mqtt_conn.error("Exception during MQTT connect: %s", e)
            return False

    def disconnect(self):
        try:
            self.client.disconnect()
            logger_mqtt_conn.info("Disconnected from MQTT broker")
        except Exception as e:
            logger_mqtt_conn.error("Exception during MQTT disconnect: %s", e)

    def close(self):
        try:
            self.client.close()
            logger_mqtt_conn.info("Closed MQTT client")
        except Exception as e:
            logger_mqtt_conn.error("Exception during MQTT close: %s", e)

    def publish(self, topic, msg, retain=False, qos=0):
        if self.is_connected():
            try:
                if self.client.publish(topic, msg, retain, qos):
                    logger_mqtt_conn.info("Published message to topic %s: %s", topic, msg)
                    return True
                else:
                    logger_mqtt_conn.error("Failed to publish message!")
                    return False
            except Exception as e:
                logger_mqtt_conn.error("Failed to publish message: %s", e)
                return False
        else:
            logger_mqtt_conn.warning("Publish called but not connected to broker")
            return False

    def subscribe(self, topic, qos=0):
        if self.is_connected():
            try:
                self.client.subscribe(topic, qos)
                logger_mqtt_conn.info("Subscribed to topic %s", topic)
                return True
            except Exception as e:
                logger_mqtt_conn.error("Failed to subscribe to topic %s: %s", topic, e)
                return False
        else:
            logger_mqtt_conn.warning("Subscribe called but not connected to broker")
            return False

    def is_connected(self):
        try:
            status = self.client.get_mqttsta()
            logger_mqtt_conn.info("MQTT connection status: %s", status)
            if status == 0:
                return True
        except Exception as e:
            logger_mqtt_conn.error("Failed to get MQTT status: %s", e)
        return False

    def get_conn_status(self):
        try:
            status = self.client.get_mqttsta()
            logger_mqtt_conn.info("MQTT connection status: %s", status)
            return status
        except Exception as e:
            logger_mqtt_conn.error("Failed to get MQTT status: %s", e)
            return -1

    def wait_msg(self):
        try:
            self.client.wait_msg()
        except Exception as e:
            logger_mqtt_conn.error("Failed to check for messages: %s", e)


# ===== FROM FILE: packet_builder.py =====
logger_packet_builder = log.getLogger("WGPS::PacketBuilder")


class PacketBuilder:

    def __init__(self):
        self.reqs = ["device_data", "time_data_gnss", "time_data_transmission", "fixation_data", "gnss_details_data", "accuracy_data", "gsm_data", "wagon_status_data", "alerts_data", "device_health_data", "control_commands_data"]
        self.device_data = {"fw": "", "devid": "", "pkst": "", "cnt": 0, "imei": ""}
        self.time_data = {"dtpf": "", "dtpt": ""}
        self.fixation_data = {"fix": "", "lat": "", "latd": "", "lng": "", "lngd": "", "alt": ""}
        self.gnss_details_data = {"nos": 0, "spd": "", "navst": 0, "sv": ""}
        self.accuracy_data = {"hdop": "", "pdop": ""}
        self.gsm_data = {"mcc": 0, "mnc": 0, "lac": 0, "cellid": 0}
        self.wagon_status_data = {"bleS": 0, "bleA": "", "bleB": "", "bleC": "", "bleD": "", "bleE": ""}
        self.alerts_data = {"tfta": 0, "recf": 0}
        self.device_health_data = {"btper": 0.0, "tmp": 0.0}
        self.control_commands_data = {"ctlg": ""}

    def update_device_data(self, fw, devid, pkst, cnt, imei):
        if pkst not in ["L", "H"]:
            raise ValueError("Invalid packet status value")
        if len(imei) > 20:
            raise ValueError("IMEI length exceeds 20 characters")
        if cnt < 0:
            raise ValueError("Packet count cannot be negative")
        if len(devid) == 0 or len(fw) == 0:
            raise ValueError("Device ID and Firmware version cannot be empty")
        self.device_data.update({"fw": str(fw), "devid": str(devid), "pkst": str(pkst), "cnt": int(cnt), "imei": str(imei)})
        self.reqs.remove("device_data")

    def is_req_device_data(self):
        return "device_data" in self.reqs

    def update_time_data_gnss(self, dtpf):
        self.time_data.update({"dtpf": str(dtpf)})
        self.reqs.remove("time_data_gnss")

    def is_req_time_data_transmission(self):
        return "time_data_transmission" in self.reqs

    def update_time_data_transmission(self, dtpt):
        self.time_data.update({"dtpt": str(dtpt)})
        self.reqs.remove("time_data_transmission")

    def is_req_time_data_gnss(self):
        return "time_data_gnss" in self.reqs

    def update_fixation_data(self, fix, lat=0.0, latd="", lng=0.0, lngd="", alt=0.0):
        if fix == "P":
            if latd not in ["N", "S"] or lngd not in ["E", "W"]:
                raise ValueError("Invalid latitude or longitude direction")
            self.fixation_data.update({"fix": "P", "lat": round(float(lat), 6), "latd": str(latd), "lng": round(float(lng), 6), "lngd": str(lngd), "alt": round(float(alt), 2)})
        else:
            self.fixation_data.update({"fix": "", "lat": "", "latd": "", "lng": "", "lngd": "", "alt": ""})
        self.reqs.remove("fixation_data")

    def is_req_fixation_data(self):
        return "fixation_data" in self.reqs

    def update_gnss_details_data(self, nos, spd, navst: bool, sv):
        self.gnss_details_data.update({"nos": int(nos), "spd": round(float(spd), 2), "navst": 1 if navst else 0, "sv": str(sv)})
        self.reqs.remove("gnss_details_data")

    def is_req_gnss_details_data(self):
        return "gnss_details_data" in self.reqs

    def update_accuracy_data(self, hdop, pdop):
        self.accuracy_data.update({"hdop": round(float(hdop), 2), "pdop": round(float(pdop), 2)})
        self.reqs.remove("accuracy_data")

    def is_req_accuracy_data(self):
        return "accuracy_data" in self.reqs

    def update_gsm_data(self, mcc, mnc, lac, cellid):
        self.gsm_data.update({"mcc": int(mcc), "mnc": int(mnc), "lac": int(lac), "cellid": int(cellid)})
        self.reqs.remove("gsm_data")

    def is_req_gsm_data(self):
        return "gsm_data" in self.reqs

    def update_wagon_status_data(self, bleS: bool, bleA="", bleB="", bleC="", bleD="", bleE=""):
        if bleS:
            self.wagon_status_data.update({"bleS": 1, "bleA": str(bleA), "bleB": str(bleB), "bleC": str(bleC), "bleD": str(bleD), "bleE": str(bleE)})
        else:
            self.wagon_status_data.update({"bleS": 0, "bleA": "", "bleB": "", "bleC": "", "bleD": "", "bleE": ""})
        self.reqs.remove("wagon_status_data")

    def is_req_wagon_status_data(self):
        return "wagon_status_data" in self.reqs

    def update_alerts_data(self, tfta: bool, recf: bool):
        self.alerts_data.update({"tfta": 1 if tfta else 0, "recf": 1 if recf else 0})
        self.reqs.remove("alerts_data")

    def is_req_alerts_data(self):
        return "alerts_data" in self.reqs

    def update_device_health_data(self, btper, tmp):
        self.device_health_data.update({"btper": round(float(btper), 2), "tmp": round(float(tmp), 2)})
        self.reqs.remove("device_health_data")

    def is_req_device_health_data(self):
        return "device_health_data" in self.reqs

    def update_control_commands_data(self, ctlg):
        self.control_commands_data.update({"ctlg": ",".join(ctlg)})
        self.reqs.remove("control_commands_data")

    def is_req_control_commands_data(self):
        return "control_commands_data" in self.reqs

    def get_pending_reqs(self):
        return self.reqs

    def build_packet(self):
        packet = {}
        packet.update(self.device_data)
        packet.update(self.time_data)
        packet.update(self.fixation_data)
        packet.update(self.gnss_details_data)
        packet.update(self.accuracy_data)
        packet.update(self.gsm_data)
        packet.update(self.wagon_status_data)
        packet.update(self.alerts_data)
        packet.update(self.device_health_data)
        packet.update(self.control_commands_data)
        return packet

    def reset_packet(self):
        self.__init__()

    def build_packet_json(self):
        packet = self.build_packet()
        return (ujson.dumps(packet), packet)


# ===== FROM FILE: power_controller.py =====
logger_power_controller = log.getLogger("WGPS::PowerController")


class PowerController:
    POWER_ON_REASON_UNKNOWN = 0
    POWER_ON_REASON_PWRKEY = 1
    POWER_ON_REASON_RESET = 2
    POWER_ON_REASON_VBAT = 3
    POWER_ON_REASON_RTC = 4
    POWER_ON_REASON_WATCHDOG_OR_ERROR = 5
    POWER_ON_REASON_VBUS = 6
    POWER_ON_REASON_CHARGING = 7
    POWER_ON_REASON_WAKEUP_PSM = 8
    POWER_ON_REASON_REBOOT_AFTER_DUMP = 9

    def __init__(self):
        self.power_on_reason = Power.powerOnReason()
        logger_power_controller.info("WGPS Power Management Initialized")

    def get_power_on_reason(self):
        return self.power_on_reason

    def power_off(self):
        logger_power_controller.info("Powering off the device...")
        Power.powerDown()

    def restart(self):
        logger_power_controller.info("Restarting the device...")
        Power.powerRestart()

    def set_autosleep(self, enable=True):
        if enable:
            logger_power_controller.info("Enabling automatic sleep mode")
            pm.autosleep(1)
        else:
            logger_power_controller.info("Disabling automatic sleep mode")
            pm.autosleep(0)


power_ctrl = PowerController()

# ===== FROM FILE: sim_controller.py =====
logger_sim_controller = log.getLogger("WGPS::SimController")


class SimController:
    SIM1 = 0
    SIM2 = 1

    def __init__(self, select_pin):
        self.select_gpio = Pin(select_pin, Pin.OUT, Pin.PULL_DISABLE, 0)
        self.selected_sim = self.SIM1

    def select_sim(self, sim_number):
        if sim_number not in [self.SIM1, self.SIM2]:
            logger_sim_controller.error("Invalid SIM number: {}".format(sim_number))
            return False
        if sim_number == self.selected_sim:
            logger_sim_controller.info("SIM {} is already selected.".format(sim_number))
            return True
        self.selected_sim = sim_number
        self.select_gpio.write(sim_number)
        logger_sim_controller.info("Selected SIM {}".format(sim_number + 1))
        return True

    def switch_sim(self):
        new_sim = self.SIM2 if self.selected_sim == self.SIM1 else self.SIM1
        return self.select_sim(new_sim)

    def get_sim_info(self):
        imsi = sim.getImsi()
        iccid = sim.getIccid()
        phone_number = sim.getPhoneNumber()
        logger_sim_controller.info("SIM Info - IMSI: {}, ICCID: {}, Phone Number: {}".format(imsi, iccid, phone_number))
        return {"imsi": imsi, "iccid": iccid, "phone_number": phone_number}

    def get_sim_status(self):
        sim_status = sim.getStatus()
        logger_sim_controller.info("SIM Status: {}".format(sim_status))
        return {"sim_status": sim_status}


# ===== FROM FILE: rtc_controller.py =====
logger_rtc_controller = log.getLogger("WGPS::RTCController")


class RTCController:

    def __init__(self):
        self.rtc = RTC()
        self.ext_rtc = None
        self.initialized = False

    def setup(self, ext_rtc=None):
        logger_rtc_controller.info("Initializing WGPS Time Module...")
        self.ext_rtc = ext_rtc
        self.rtc_synced = self.verify_sync()
        self.initialized = True
        logger_rtc_controller.info("WGPS Time Module Initialized!")

    def update_time(self, year, month, day, hour, minute, second):
        if not self.initialized:
            logger_rtc_controller.error("RTCController not initialized.")
            return False
        if self.rtc.datetime([year, month, day, 0, hour, minute, second, 0]) < 0:
            logger_rtc_controller.error("Failed to update internal RTC.")
            return False
        if self.ext_rtc is not None:
            if not self.ext_rtc.set_time(year, month, day, hour, minute, second):
                logger_rtc_controller.error("Failed to update external RTC.")
                return False
            self.rtc_synced = self.verify_sync()
        else:
            logger_rtc_controller.warning("No external RTC configured, skipping external RTC update.")
        logger_rtc_controller.info("RTC updated successfully.")
        return True

    def set_datetime_from_gnss(self, datetime_str, capture_ts):
        try:
            date_time = datetime_str.split("T")
            date_parts = date_time[0].split("-")
            time_parts = date_time[1].split("+")[0].split(":")
            year = int(date_parts[0])
            month = int(date_parts[1])
            day = int(date_parts[2])
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            second = int(time_parts[2])
            timestamp = utime.mktime((year, month, day, hour, minute, second, 0, 0))
            local_ts = self.get_timestamp()
            local_ts_compensated = local_ts - (utime.time() - capture_ts)
            last_upd = self.get_last_update_time()
            last_upd = last_upd if last_upd else 0
            if abs(timestamp - local_ts_compensated) < 10 and local_ts - last_upd < 2592000:
                logger_rtc_controller.info("GNSS time matches RTC time, no update needed.")
                return True
            timestamp += utime.time() - capture_ts
            year, month, day, hour, minute, second = utime.localtime(timestamp)[:6]
            if self.update_time(year, month, day, hour, minute, second):
                return self.set_last_update_time()
            return False
        except Exception as e:
            logger_rtc_controller.error("Error setting datetime from GNSS: {}".format(e))
            return False

    def get_timestamp(self):
        if not self.initialized:
            logger_rtc_controller.error("RTCController not initialized.")
            return False
        try:
            year, month, day, hour, minute, second = self.get_time()
            timestamp = utime.mktime((year, month, day, hour, minute, second, 0, 0))
            logger_rtc_controller.info("Current RTC timestamp: {}".format(timestamp))
            return timestamp
        except Exception as e:
            logger_rtc_controller.error("Error getting RTC timestamp: {}".format(e))
            return False

    def get_time(self):
        if not self.initialized:
            logger_rtc_controller.error("RTCController not initialized.")
            return False
        try:
            if not self.rtc_synced:
                logger_rtc_controller.warning("RTC is not synchronized!")
            year, month, day, week, hour, minute, second, microsecond = self.rtc.datetime()
            logger_rtc_controller.info("Current RTC time: {}-{}-{} {}:{}:{}".format(year, month, day, hour, minute, second))
            return (year, month, day, hour, minute, second)
        except Exception as e:
            logger_rtc_controller.error("Error getting RTC time: {}".format(e))
            return False

    def get_time_string(self):
        if not self.initialized:
            logger_rtc_controller.error("RTCController not initialized.")
            return ("", ())
        try:
            year, month, day, hour, minute, second = self.get_time()
            time_format = str(wgps_configs.get_config("time.time_format"))
            time_str = time_format.format(year, month, day, hour, minute, second, wgps_configs.get_config("time.time_zone_offset"))
            return (time_str, (year, month, day, hour, minute, second))
        except Exception as e:
            logger_rtc_controller.error("Error getting RTC time string: {}".format(e))
            return ("", ())

    def verify_sync(self):
        try:
            if self.ext_rtc is None:
                logger_rtc_controller.warning("No external RTC configured for sync verification.")
                return True
            ext_time = self.ext_rtc.get_time()
            if not ext_time:
                logger_rtc_controller.error("Failed to get time from external RTC.")
                return False
            current_time = self.get_time()
            if current_time and ext_time:
                if current_time[:5] == ext_time[:5] and abs(current_time[5] - ext_time[5]) <= 30:
                    logger_rtc_controller.info("RTC and external RTC are synchronized.")
                    return True
                else:
                    logger_rtc_controller.warning("RTC and external RTC are not synchronized.")
                    return False
            else:
                logger_rtc_controller.warning("Could not retrieve time for comparison.")
                return False
        except Exception as e:
            logger_rtc_controller.error("Error verifying RTC sync: {}".format(e))
            return False

    def get_last_update_time(self):
        try:
            return wgps_configs.get_config("time.last_update")
        except Exception as e:
            logger_rtc_controller.error("Error getting last update time: {}".format(e))
            return False

    def set_last_update_time(self):
        try:
            year, month, day, hour, minute, second = self.get_time()
            last_update_dt = utime.mktime((year, month, day, hour, minute, second, 0, 0))
            wgps_configs.set_config("time.last_update", last_update_dt)
            logger_rtc_controller.info("Last update time set to: {}".format(last_update_dt))
            return True
        except Exception as e:
            logger_rtc_controller.error("Error setting last update time: {}".format(e))
            return False

    def set_alarm(self, datetime_list):
        if not self.initialized:
            logger_rtc_controller.error("RTCController not initialized.")
            return False
        try:
            if self.rtc.set_alarm(datetime_list) != 0:
                logger_rtc_controller.error("Failed to set RTC alarm.")
                return False
            self.rtc.enable_alarm(1)
            return True
        except Exception as e:
            logger_rtc_controller.error("Error setting RTC alarm: {}".format(e))
            return False

    def clear_alarm(self):
        if not self.initialized:
            logger_rtc_controller.error("RTCController not initialized.")
            return False
        try:
            if self.rtc.enable_alarm(0) == 0:
                logger_rtc_controller.info("RTC alarm cleared.")
                return True
            else:
                logger_rtc_controller.error("Failed to clear RTC alarm.")
                return False
        except Exception as e:
            logger_rtc_controller.error("Error clearing RTC alarm: {}".format(e))
            return False


# ===== FROM FILE: sdcard_controller.py =====
logger_sdcard_controller = log.getLogger("WGPS::SDCard")


class SDCard:

    def __init__(self):
        self.mounted = False
        self.sd_en = Pin(wgps_configs.get_config("system.sd_card.enable_pin"), Pin.OUT, Pin.PULL_DISABLE, 0)

    def mount(self):
        if self.mounted:
            return True
        try:
            self.sd_en.write(1)
            utime.sleep(1)
            self.udev = uos.VfsSd("sd_fs")
            uos.mount(self.udev, wgps_configs.get_config("system.storage.root"))
            logger_sdcard_controller.info("SD Info: {}".format(uos.statvfs(wgps_configs.get_config("system.storage.root"))))
            self.mounted = True
            logger_sdcard_controller.info("SD card mounted successfully.")
        except Exception as e:
            logger_sdcard_controller.error("Failed to mount SD card: {}".format(e))
            self.mounted = False
        return self.mounted

    def unmount(self):
        try:
            if self.mounted:
                self.sd_en.write(0)
        except Exception as e:
            logger_sdcard_controller.error("Failed to unmount SD card: {}".format(e))
        self.mounted = False

    def format(self):
        try:
            if self.mounted:
                items = uos.listdir(wgps_configs.get_config("system.storage.root"))
                for item in items:
                    logger_sdcard_controller.info("Removing file: {}".format(item))
                    try:
                        item_path = "{}/{}".format(wgps_configs.get_config("system.storage.root"), item)
                        uos.remove(item_path)
                    except Exception as e:
                        logger_sdcard_controller.error("Failed to remove file {}: {}".format(item, e))
                logger_sdcard_controller.info("SD card formatted successfully.")
                return True
            else:
                logger_sdcard_controller.error("SD card not mounted. Cannot format.")
                return False
        except Exception as e:
            logger_sdcard_controller.error("Failed to format SD card: {}".format(e))
            return False

    def __del__(self):
        self.unmount()


# ===== FROM FILE: ds3231.py =====
class DS3231:

    def __init__(self, i2c_channel, address=104):
        self.comm = EC200U_I2C(i2c_channel, address)

    def _bcd_to_int(self, bcd):
        return (bcd >> 4) * 10 + (bcd & 15)

    def _int_to_bcd(self, val):
        return val // 10 << 4 | val % 10

    def get_time(self):
        """
        Returns (year, month, day, hour, minute, second)
        """
        data = self.comm.read_register(0, 7)
        second = self._bcd_to_int(data[0])
        minute = self._bcd_to_int(data[1])
        hour = self._bcd_to_int(data[2] & 63)
        day = self._bcd_to_int(data[4])
        month = self._bcd_to_int(data[5] & 31)
        year = 2000 + self._bcd_to_int(data[6])
        return (year, month, day, hour, minute, second)

    def set_time(self, year, month, day, hour, minute, second):
        """
        Set time on DS3231 RTC.
        """
        year_bcd = self._int_to_bcd(year % 100)
        month_bcd = self._int_to_bcd(month)
        day_bcd = self._int_to_bcd(day)
        hour_bcd = self._int_to_bcd(hour)
        minute_bcd = self._int_to_bcd(minute)
        second_bcd = self._int_to_bcd(second)
        data = bytes([second_bcd, minute_bcd, hour_bcd, 0, day_bcd, month_bcd, year_bcd])
        self.comm.write_register(0, data)
        return True

    def get_temperature(self):
        """
        Read temperature from DS3231 (in Celsius).
        """
        data = self.comm.read_register(17, 2)
        temp_msb = data[0]
        temp_lsb = data[1] >> 6
        temp = temp_msb + temp_lsb * 0.25
        return temp


# ===== FROM FILE: nrf_controller.py =====
logger_nrf_controller = log.getLogger("WGPS::NRFController")


class NRFController:
    READ_STATUS = 1
    WRITE_STATUS = 2
    CLEAR_FLAGS = 3
    SET_SENSOR_DISABLE_BITS = 4
    SET_BLE_ENTRY = 16
    GET_BLE_DATA = 17
    START_BLE_READ_ALL = 18
    ENABLE_BLE = 19
    DISABLE_BLE = 20
    REQUEST_SLEEP = 21
    GET_OPERATION_STATUS = 32
    SENSOR_FLAGS_MASK = [0, 0, 0, 255]

    def __init__(self, i2c_channel, address, en_gpio):
        self.sensor_flags = {"TMAG1": False, "TMAG2": False, "LDR": False, "SHT30": False, "DS3231": False, "LSM6DSL": False}
        try:
            self.nrf_enable_gpio = Pin(en_gpio, Pin.OUT, Pin.PULL_DISABLE, 0)
            self.i2c = EC200U_I2C(i2c_channel, address)
        except Exception as e:
            raise RuntimeError("NRFComm init failed: {}".format(e))

    def read_regs(self, reg_addr, length=1):
        try:
            data = self.i2c.read_register(reg_addr, length)
            return bytearray(data)
        except Exception as e:
            raise RuntimeError("NRFComm read_regs failed: {}".format(e))

    def write_regs(self, reg_addr, data):
        try:
            self.i2c.write_register(reg_addr, data)
        except Exception as e:
            raise RuntimeError("NRFComm write_regs failed: {}".format(e))

    def read_reg32(self, reg_addr, endian="little", signed=False):
        try:
            raw = self.read_regs(reg_addr, 4)
            if len(raw) != 4:
                raise RuntimeError("expected 4 bytes, got {}".format(len(raw)))
            return int.from_bytes(bytes(raw), endian, signed)
        except Exception as e:
            raise RuntimeError("NRFComm read_reg32 failed: {}".format(e))

    def wakeup_nrf(self):
        try:
            self.nrf_enable_gpio.value(1)
            utime.sleep_ms(50)
            self.nrf_enable_gpio.value(0)
            logger_nrf_controller.info("NRF wakeup sequence complete.")
        except Exception as e:
            raise RuntimeError("NRFComm wakeup_nrf failed: {}".format(e))

    def request_sleep(self):
        try:
            self.write_regs(self.REQUEST_SLEEP, bytearray(1))
            logger_nrf_controller.info("NRF sleep requested.")
        except Exception as e:
            raise RuntimeError("NRFComm request_sleep failed: {}".format(e))

    def get_status(self):
        try:
            status = self.read_reg32(self.READ_STATUS)
            return status
        except Exception as e:
            raise RuntimeError("NRFComm get_status failed: {}".format(e))

    def parse_sensor_flags(self, flags_reg):
        self.sensor_flags["TMAG1"] = bool(flags_reg & 1)
        self.sensor_flags["TMAG2"] = bool(flags_reg >> 1 & 1)
        self.sensor_flags["LDR"] = bool(flags_reg >> 2 & 1)
        return self.sensor_flags

    def clear_sensor_flags(self):
        try:
            self.write_regs(self.CLEAR_FLAGS, bytearray(self.SENSOR_FLAGS_MASK))
        except Exception as e:
            raise RuntimeError("NRFComm clear_sensor_flags failed: {}".format(e))

    def disable_interrupts(self, disable_tmag1=False, disable_tmag2=False, disable_ldr=False, disable_sht30=False, disable_ds3231=False, disable_lsm6dsl=False):
        disable_bits = 0
        if disable_tmag1:
            disable_bits |= 1 << 0
        if disable_tmag2:
            disable_bits |= 1 << 1
        if disable_ldr:
            disable_bits |= 1 << 2
        if disable_sht30:
            disable_bits |= 1 << 3
        if disable_ds3231:
            disable_bits |= 1 << 4
        if disable_lsm6dsl:
            disable_bits |= 1 << 5
        try:
            payload = bytearray(1)
            payload[0] = disable_bits & 255
            self.write_regs(self.SET_SENSOR_DISABLE_BITS, payload)
        except Exception as e:
            raise RuntimeError("NRFComm disable_interrupts failed: {}".format(e))


# ===== FROM FILE: tmag5273.py =====
logger_tmag5273 = log.getLogger("WGPS::TMAG5273")


class TMAG5273:
    REG_DEVICE_CONFIG_1 = 0
    REG_DEVICE_CONFIG_2 = 1
    REG_SENSOR_CONFIG_1 = 2
    REG_SENSOR_CONFIG_2 = 3
    REG_X_THR = 4
    REG_Y_THR = 5
    REG_Z_THR = 6
    REG_T_THR = 7
    REG_INT_CONFIG_1 = 8
    REG_MAG_GAIN_CONFIG = 9
    REG_MAG_OFFSET_CONFIG_1 = 10
    REG_MAG_OFFSET_CONFIG_2 = 11
    REG_I2C_ADDRESS = 12
    REG_DEVICE_ID = 13
    REG_MANUFACTURER_ID_L = 14
    REG_MANUFACTURER_ID_H = 15
    REG_TEMP_MSB = 16
    REG_TEMP_LSB = 17
    REG_X_MSB = 18
    REG_X_LSB = 19
    REG_Y_MSB = 20
    REG_Y_LSB = 21
    REG_Z_MSB = 22
    REG_Z_LSB = 23
    REG_CONV_STATUS = 24
    REG_ANGLE_MSB = 25
    REG_ANGLE_LSB = 26
    REG_MAGNITUDE = 27
    REG_DEVICE_STATUS = 28

    def __init__(self, channel, address, check_id=True):
        try:
            self.i2c = EC200U_I2C(channel, address)
        except Exception as e:
            raise RuntimeError("Failed to initialize TMAG5273 I2C: {}".format(e))
        if check_id:
            if not self.check_device():
                raise RuntimeError("TMAG5273 device not found at address 0x{:02X}".format(address))
        self.set_mode("sleep")

    def check_device(self):
        for retry in range(3):
            devid = self.read_register(self.REG_DEVICE_ID)
            manid_l = self.read_register(self.REG_MANUFACTURER_ID_L)
            manid_h = self.read_register(self.REG_MANUFACTURER_ID_H)
            manid = manid_h << 8 | manid_l
            if devid == 6 and manid == 21577:
                return True
            elif retry == 2:
                logger_tmag5273.error("TMAG5273 device not found!")
                return False

    def set_mode(self, mode):
        val = self.read_register(self.REG_DEVICE_CONFIG_2)
        if mode == "standby":
            val = val & 252
        elif mode == "active":
            val = val & 252 | 2
        elif mode == "sleep":
            val = val & 252 | 1
        elif mode == "low_power":
            val = val | 3
        else:
            raise ValueError("Invalid mode specified: {}".format(mode))
        self.write_register(self.REG_DEVICE_CONFIG_2, val)

    def configure_interrupt(self, thresholds, alert, bidir):
        logger_tmag5273.info("Configuring TMAG5273 interrupt with thresholds: {}, alert: {}, bidirectional: {}".format(thresholds, alert, bidir))
        self.write_register(self.REG_DEVICE_CONFIG_1, 0)
        self.write_register(self.REG_SENSOR_CONFIG_1, 121)
        self.set_mag_range("low")
        self.set_mag_threshold_mode(alert)
        self.set_mag_thresholds(thresholds["x"], thresholds["y"], thresholds["z"])
        self.write_register(self.REG_INT_CONFIG_1, 72)
        if bidir:
            self.write_register(self.REG_DEVICE_CONFIG_2, 35)
        else:
            self.write_register(self.REG_DEVICE_CONFIG_2, 3)

    def clear_interrupt(self):
        self.write_register(self.REG_SENSOR_CONFIG_1, 0)
        self.set_mag_thresholds(0, 0, 0)
        self.write_register(self.REG_INT_CONFIG_1, 0)

    def set_mag_axis_enable(self, x_en, y_en, z_en):
        val = self.read_register(self.REG_SENSOR_CONFIG_1)
        axis_bits = 0
        if x_en and y_en and z_en:
            axis_bits = 7
        elif x_en and (not y_en) and (not z_en):
            axis_bits = 1
        elif not x_en and y_en and (not z_en):
            axis_bits = 2
        elif not x_en and (not y_en) and z_en:
            axis_bits = 4
        else:
            raise ValueError("Invalid combination of axis enables")
        axis_bits = axis_bits << 4
        val = val & 15 | axis_bits
        self.write_register(self.REG_SENSOR_CONFIG_1, val)

    def set_conv_avg(self, samples):
        if samples not in [1, 2, 4, 8, 16, 32]:
            raise ValueError("samples must be one of [1, 2, 4, 8, 16, 32]")
        val = self.read_register(self.REG_SENSOR_CONFIG_1)
        val = val & 227
        sample_bits = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5}
        val = val | sample_bits[samples] << 2
        self.write_register(self.REG_SENSOR_CONFIG_1, val)

    def write_register(self, reg_addr, value):
        self.i2c.write_register(reg_addr, bytearray([value & 255]))

    def read_register(self, reg_addr):
        return self.i2c.read_register(reg_addr, 1)[0]

    def _twos_complement(self, val, bits):
        if val & 1 << bits - 1 != 0:
            val = val - (1 << bits)
        return val

    def get_mag_values(self):
        range = 266 if self.get_mag_range() == "high" else 133
        while True:
            status = self.read_register(self.REG_CONV_STATUS)
            utime.sleep_ms(100)
            if status & 1:
                break
        values = []
        for i in range(0, 6, 2):
            msb = self.read_register(self.REG_X_MSB + i)
            lsb = self.read_register(self.REG_X_LSB + i)
            val = self._twos_complement(msb << 8 | lsb, 16)
            values.append(val * range / 32768.0)
        return values

    def set_mag_thresholds(self, x_thr, y_thr, z_thr):
        enc_th_x = self.encode_mag_threshold(x_thr)
        enc_th_y = self.encode_mag_threshold(y_thr)
        enc_th_z = self.encode_mag_threshold(z_thr)
        self.write_register(self.REG_X_THR, enc_th_x & 255)
        self.write_register(self.REG_Y_THR, enc_th_y & 255)
        self.write_register(self.REG_Z_THR, enc_th_z & 255)

    def get_mag_thresholds(self):
        x_code = self.read_register(self.REG_X_THR)
        y_code = self.read_register(self.REG_Y_THR)
        z_code = self.read_register(self.REG_Z_THR)
        x_thr = self.decode_mag_threshold(x_code)
        y_thr = self.decode_mag_threshold(y_code)
        z_thr = self.decode_mag_threshold(z_code)
        return (x_thr, y_thr, z_thr)

    def set_mag_threshold_mode(self, mode):
        tmp = self.read_register(self.REG_SENSOR_CONFIG_2)
        if mode == "over":
            tmp &= ~32
        elif mode == "under":
            tmp |= 32
        else:
            raise ValueError("mode must be 'over' or 'under'")
        self.write_register(self.REG_SENSOR_CONFIG_2, tmp)

    def set_mag_range(self, range):
        if range == "low":
            val = self.read_register(self.REG_SENSOR_CONFIG_2)
            self.write_register(self.REG_SENSOR_CONFIG_2, val & ~3)
        elif range == "high":
            val = self.read_register(self.REG_SENSOR_CONFIG_2)
            self.write_register(self.REG_SENSOR_CONFIG_2, val | 3)
        else:
            raise ValueError("range must be 'low' or 'high'")

    def get_mag_range(self):
        val = self.read_register(self.REG_SENSOR_CONFIG_2)
        if val & 3 == 0:
            return "low"
        elif val & 3 == 3:
            return "high"
        else:
            raise ValueError("Invalid magnetic range configuration")

    def encode_mag_threshold(self, value):
        rng = 1 if self.get_mag_range() == "high" else 0
        return int(value * 128 / (133 * (1 + rng)))

    def decode_mag_threshold(self, code):
        rng = 1 if self.get_mag_range() == "high" else 0
        return code * (133 * (1 + rng)) / 128.0

    def encode_temp_threshold(self, value):
        if value < -41 or value > 170:
            raise ValueError("Temperature threshold out of range (-41 to 170 C)")
        code = int((value + 41) / 8) + 26
        return code

    def decode_temp_threshold(self, code):
        return (code - 26) * 8 - 41

    def get_temperature_raw(self):
        msb = self.read_register(self.REG_TEMP_MSB)
        lsb = self.read_register(self.REG_TEMP_LSB)
        val = msb << 8 | lsb
        return self._twos_complement(val, 16)

    def get_temperature(self):
        ECHAR_T_ADC_T0 = 17508.0
        ECHAR_T_SENS_T0 = 25.0
        ECHAR_T_ADC_RES = 60.1
        raw = self.get_temperature_raw()
        return ECHAR_T_SENS_T0 + (float(raw) - ECHAR_T_ADC_T0) / ECHAR_T_ADC_RES

    def enable_temperature(self):
        try:
            val = self.read_register(self.REG_T_THR)
            val |= 1
            self.write_register(self.REG_T_THR, val)
            logger_tmag5273.info("Temperature channel enabled")
            return True
        except Exception as e:
            logger_tmag5273.error("Failed to enable temperature channel: %s", e)
            return False

    def disable_temperature(self):
        try:
            val = self.read_register(self.REG_T_THR)
            val &= ~1
            self.write_register(self.REG_T_THR, val)
            logger_tmag5273.info("Temperature channel disabled")
            return True
        except Exception as e:
            logger_tmag5273.error("Failed to disable temperature channel: %s", e)
            return False

    def set_temperature_threshold(self, value, reset=False):
        tmp = self.read_register(self.REG_T_THR)
        if reset:
            tmp = tmp & 1
        else:
            enc_th = self.encode_temp_threshold(value)
            tmp = enc_th << 1 | tmp & 1
        self.write_register(self.REG_T_THR, tmp)

    def get_temperature_threshold(self):
        code = self.read_register(self.REG_T_THR)
        code = code >> 1
        return self.decode_temp_threshold(code)


# ===== FROM FILE: packet_data.py =====
logger_packet_data = log.getLogger("WGPS::PacketData")


class PacketData(GenericData):
    HISTORY = "H"
    LIVE = "L"

    def __init__(self):
        super().__init__()
        self.pkst = self.LIVE

    def get_data(self):
        return self.data

    def set_data(self, data):
        try:
            self.pkst = data["pkst"]
            self.data = data
        except KeyError as e:
            logger_packet_data.error("Invalid data format: {}".format(e))

    def update_state(self, pkst):
        if pkst in [self.HISTORY, self.LIVE]:
            self.pkst = pkst
            self.data["pkst"] = pkst
            return True
        else:
            logger_packet_data.error("Invalid state pkst: {}".format(pkst))
            return False

    def get_state(self):
        return self.pkst


# ===== FROM FILE: location_manager.py =====
class LocationData(GenericData):

    def __init__(self):
        super().__init__()


logger_location_manager = log.getLogger("WGPS::LocationManager")


class LocationManager(Manager):

    def __init__(self):
        super().__init__()
        self.gnss = L89(UART.UART2, wgps_configs.get_config("system.gnss.fix3d_pin"), wgps_configs.get_config("system.gnss.enable_pin"), wgps_configs.get_config("system.gnss.reset_pin"), wgps_configs.get_config("system.gnss.wakeup_pin"), wgps_configs.get_config("system.gnss.baudrate"))
        self.thread = None

    def start(self):
        if self.thread is not None:
            logger_location_manager.warning("LocationManager thread already running")
            return
        self.keep_running = True
        self.thread_joined = False
        self.thread = _thread.start_new_thread(self.thread_func, ())

    def stop(self):
        if self.thread is not None:
            self.keep_running = False
            while not self.thread_joined:
                utime.sleep(1)
            self.thread = None

    def get_data(self):
        return self.gnss.get_data()

    def thread_func(self):
        self.gnss.start()
        while self.keep_running:
            if self.gnss.is_ready():
                self.ready = True
                break
            utime.sleep(1)
        self.gnss.stop()
        self.thread_joined = True


# ===== FROM FILE: sys_diag_manager.py =====
logger_sys_diag_manager = log.getLogger("WGPS::SysDiagManager")


class SysDiagData(GenericData):

    def __init__(self):
        super().__init__()


class SysDiagnosticsManager(Manager):

    def __init__(self):
        super().__init__()
        self.thread = None

    def start(self):
        pass

    def stop(self):
        pass

    def is_ready(self):
        return self.ready

    def get_data(self):
        pass

    def thread_func(self):
        pass

    def loop(self):
        pass


# ===== FROM FILE: comm_manager.py =====
logger_comm_manager = log.getLogger("WGPS::CommManager")


class CommunicationManager(Manager):
    READY = 0
    BUSY = 1
    ERROR = 2
    PRIMARY = "primary"
    SECONDARY = "secondary"

    def __init__(self, network_manager):
        super().__init__()
        self.network_manager = network_manager
        self.initialized = False
        self.conn_primary = None
        self.conn_secondary = None
        self.en_conn_primary = False
        self.en_conn_secondary = False
        self.status = {self.PRIMARY: self.READY, self.SECONDARY: self.READY}
        self.thread = None
        self.queue = Queue(0)
        self.resp_queue = Queue(0)
        self.cb = None
        self.listener_thread = None
        self.listener_thread_keep_running = True
        self.listener_thread_joined = False
        self.publisher_thread = None
        self.publisher_thread_keep_running = True
        self.publisher_thread_joined = False

    def init_connections(self, en_conn_primary, en_conn_secondary, cb=None):
        self.en_conn_primary = en_conn_primary
        self.en_conn_secondary = en_conn_secondary
        if self.en_conn_primary:
            cert_file = wgps_configs.get_config("mqtt.primary.cert_file")
            key_file = wgps_configs.get_config("mqtt.primary.key_file")
            if cert_file is None or key_file is None or len(cert_file) == 0 or (len(key_file) == 0):
                cert_path = None
                key_path = None
            else:
                cert_path = "{}/{}".format(wgps_configs.get_config("system.certs.primary"), cert_file)
                key_path = "{}/{}".format(wgps_configs.get_config("system.certs.primary"), key_file)
            self.conn_primary = MQTTConnection(client_id=wgps_configs.get_config("mqtt.primary.client_id"), broker=wgps_configs.get_config("mqtt.primary.broker"), port=wgps_configs.get_config("mqtt.primary.port"), username=wgps_configs.get_config("mqtt.primary.username"), password=wgps_configs.get_config("mqtt.primary.password"), ssl_en=wgps_configs.get_config("mqtt.primary.ssl_en"), cert_path=cert_path, key_path=key_path)
        if self.en_conn_secondary:
            cert_file = wgps_configs.get_config("mqtt.secondary.cert_file")
            key_file = wgps_configs.get_config("mqtt.secondary.key_file")
            if cert_file is None or key_file is None or len(cert_file) == 0 or (len(key_file) == 0):
                cert_path = None
                key_path = None
            else:
                cert_path = "{}/{}".format(wgps_configs.get_config("system.certs.secondary"), cert_file)
                key_path = "{}/{}".format(wgps_configs.get_config("system.certs.secondary"), key_file)
            self.conn_secondary = MQTTConnection(client_id=wgps_configs.get_config("mqtt.secondary.client_id"), broker=wgps_configs.get_config("mqtt.secondary.broker"), port=wgps_configs.get_config("mqtt.secondary.port"), username=wgps_configs.get_config("mqtt.secondary.username"), password=wgps_configs.get_config("mqtt.secondary.password"), ssl_en=wgps_configs.get_config("mqtt.secondary.ssl_en"), cert_path=cert_path, key_path=key_path, recv_cb=self.mqtt_recv_cb)
            self.cb = cb
        self.initialized = True

    def start(self):
        if not self.initialized:
            logger_comm_manager.error("CommunicationManager not initialized!")
            return
        if self.thread is not None:
            logger_comm_manager.warning("CommunicationManager thread already running")
            return
        self.keep_running = True
        self.thread_joined = False
        self.thread = _thread.start_new_thread(self.main_thread_func, ())

    def stop(self):
        if not self.initialized:
            logger_comm_manager.error("CommunicationManager not initialized!")
            return
        self.keep_running = False
        if self.thread is not None:
            st = utime.time()
            while not self.thread_joined and utime.time() - st < 3:
                utime.sleep(1)
            if not self.thread_joined:
                _thread.stop_thread(self.thread)
            self.thread = None
            gc.collect()
        if self.en_conn_primary:
            self.conn_primary.disconnect()
        if self.en_conn_secondary:
            self.conn_secondary.disconnect()
        utime.sleep(1)
        self.ready = False

    def send_data(self, msg, block=True, timeout=None, pub_topic=None):
        if not self.initialized:
            logger_comm_manager.error("CommunicationManager not initialized!")
            return False
        start = utime.time()
        while (self.status[self.PRIMARY] != self.READY or self.status[self.SECONDARY] != self.READY) and (timeout is None or utime.time() - start < timeout):
            utime.sleep_ms(250)
        if self.status[self.PRIMARY] != self.READY or self.status[self.SECONDARY] != self.READY:
            logger_comm_manager.error("Connection not ready for sending")
            return False
        elif timeout is not None and utime.time() - start >= timeout:
            logger_comm_manager.error("Timeout waiting for connection to be ready")
            return False
        if timeout is not None:
            timeout -= utime.time() - start
        try:
            start = utime.time()
            while True:
                if self.queue.put((msg, pub_topic)):
                    if self.en_conn_primary:
                        self.status[self.PRIMARY] = self.BUSY
                    if self.en_conn_secondary:
                        self.status[self.SECONDARY] = self.BUSY
                    return True
                elif not block:
                    logger_comm_manager.error("Failed to enqueue message for sending: Queue full")
                    return False
                if timeout is not None and utime.time() - start >= timeout:
                    logger_comm_manager.error("Timeout waiting to enqueue message for sending")
                    return False
                utime.sleep_ms(250)
        except Exception as e:
            logger_comm_manager.error("Failed to enqueue message for sending: %s", e)
        return False

    def send_response(self, msg, block=True, timeout=None, pub_topic=None):
        if not self.initialized:
            logger_comm_manager.error("CommunicationManager not initialized!")
            return False
        start = utime.time()
        while self.status[self.SECONDARY] != self.READY and (timeout is None or utime.time() - start < timeout):
            utime.sleep_ms(250)
        if self.status[self.SECONDARY] != self.READY:
            logger_comm_manager.error("Secondary connection not ready for sending response")
            return False
        elif timeout is not None and utime.time() - start >= timeout:
            logger_comm_manager.error("Timeout waiting for secondary connection to be ready")
            return False
        if timeout is not None:
            timeout -= utime.time() - start
        try:
            start = utime.time()
            while True:
                if self.resp_queue.put((msg, pub_topic)):
                    return True
                elif not block:
                    logger_comm_manager.error("Failed to enqueue response message for sending: Queue full")
                    return False
                if timeout is not None and utime.time() - start >= timeout:
                    logger_comm_manager.error("Timeout waiting to enqueue response message for sending")
                    return False
                utime.sleep_ms(250)
        except Exception as e:
            logger_comm_manager.error("Failed to enqueue response message for sending: %s", e)
            return False

    def send_json(self, obj, block=True, timeout=None, response=False, pub_topic=None):
        if not self.initialized:
            logger_comm_manager.error("CommunicationManager not initialized!")
            return
        try:
            msg = ujson.dumps(obj)
        except Exception as e:
            logger_comm_manager.error("Failed to serialize object to JSON: %s", e)
            return False
        if response:
            return self.send_response(msg, block, timeout, pub_topic)
        else:
            return self.send_data(msg, block, timeout, pub_topic)

    def get_status(self):
        if not self.initialized:
            logger_comm_manager.error("CommunicationManager not initialized!")
            return None
        return self.status

    def wait_until_finished(self, timeout=None):
        if not self.initialized:
            logger_comm_manager.error("CommunicationManager not initialized!")
            return False
        start = utime.time()
        while (self.status[self.PRIMARY] == self.BUSY or self.status[self.SECONDARY] == self.BUSY) and (timeout is None or utime.time() - start < timeout):
            utime.sleep_ms(500)
        if self.status[self.PRIMARY] == self.BUSY or self.status[self.SECONDARY] == self.BUSY:
            logger_comm_manager.error("Timeout waiting for communication to finish")
            return False
        return True

    def reset_status(self):
        if not self.initialized:
            logger_comm_manager.error("CommunicationManager not initialized!")
            return
        if self.en_conn_primary:
            self.status[self.PRIMARY] = self.READY
        if self.en_conn_secondary:
            self.status[self.SECONDARY] = self.READY

    def mqtt_recv_cb(self, topic, msg):
        logger_comm_manager.info("Received MQTT message on topic %s\n", topic.decode("utf-8"))
        if self.cb is not None:
            self.cb(topic.decode("utf-8"), msg.decode("utf-8"))

    def main_thread_func(self):
        while self.keep_running:
            self.listener_thread = None
            self.listener_thread_keep_running = True
            self.listener_thread_joined = False
            self.publisher_thread = None
            self.publisher_thread_keep_running = True
            self.publisher_thread_joined = False
            logger_comm_manager.info("Starting listener thread...")
            stacksize = _thread.stack_size()
            _thread.stack_size(16 * 1024)
            self.listener_thread = _thread.start_new_thread(self.listener_thread_func, ())
            _thread.stack_size(stacksize)
            while self.queue.empty() and self.keep_running:
                utime.sleep(1)
            logger_comm_manager.info("Stopping listener thread...")
            self.listener_thread_keep_running = False
            st = utime.time()
            while not self.listener_thread_joined and utime.time() - st < 3:
                utime.sleep(1)
            if not self.listener_thread_joined:
                _thread.stop_thread(self.listener_thread)
            self.listener_thread = None
            gc.collect()
            if not self.keep_running:
                break
            logger_comm_manager.info("Starting publisher thread...")
            stacksize = _thread.stack_size()
            _thread.stack_size(16 * 1024)
            self.publisher_thread = _thread.start_new_thread(self.publisher_thread_func, ())
            _thread.stack_size(stacksize)
            while self.keep_running:
                if self.publisher_thread_joined:
                    break
                utime.sleep(1)
            logger_comm_manager.info("Stopping publisher thread...")
            self.publisher_thread_keep_running = False
            st = utime.time()
            while not self.publisher_thread_joined and utime.time() - st < 3:
                utime.sleep(1)
            if not self.publisher_thread_joined:
                _thread.stop_thread(self.publisher_thread)
            self.publisher_thread = None
            gc.collect()
            logger_comm_manager.info("Cycle complete, restarting...")
        if self.listener_thread is not None and (not self.listener_thread_joined):
            _thread.stop_thread(self.listener_thread)
        if self.publisher_thread is not None and (not self.publisher_thread_joined):
            _thread.stop_thread(self.publisher_thread)
        self.listener_thread = None
        self.publisher_thread = None
        gc.collect()
        self.thread_joined = True

    def response_thread_func(self):
        while self.listener_thread_keep_running:
            if not self.resp_queue.empty():
                resp, pub_topic = self.resp_queue.get()
                if resp is not None:
                    logger_comm_manager.info("Sending response message...")
                    topic = pub_topic if pub_topic is not None else wgps_configs.get_config("mqtt.secondary.resp_topic")
                    self.conn_secondary.publish(topic, resp, qos=wgps_configs.get_config("mqtt.secondary.qos"))
                    logger_comm_manager.info("Response message sent.")
            else:
                utime.sleep(1)

    def listener_thread_func(self):
        while self.listener_thread_keep_running:
            while not self.network_manager.is_ready() and self.listener_thread_keep_running:
                utime.sleep(1)
            logger_comm_manager.info("Network ready, connecting to secondary MQTT...")
            while self.listener_thread_keep_running:
                if self.conn_secondary.connect():
                    break
                else:
                    logger_comm_manager.warning("{} - MQTT connect failed, retrying...".format(self.SECONDARY))
                    if self.conn_secondary.get_conn_status() not in [0, 1] and self.listener_thread_keep_running:
                        utime.sleep(1)
                    else:
                        break
            while self.conn_secondary.is_connected() is False and self.listener_thread_keep_running:
                logger_comm_manager.warning("{} - MQTT not connected, waiting...".format(self.SECONDARY))
                utime.sleep(1)
            if self.conn_secondary.is_connected():
                self.ready = True
                self.response_thread = _thread.start_new_thread(self.response_thread_func, ())
                sub_topic = wgps_configs.get_config("mqtt.secondary.sub_topic")
                if sub_topic is not None and len(sub_topic) > 0:
                    self.conn_secondary.subscribe(sub_topic, wgps_configs.get_config("mqtt.secondary.qos"))
                logger_comm_manager.info("{} - Listening for incoming messages...".format(self.SECONDARY))
                while self.listener_thread_keep_running:
                    while self.conn_secondary.is_connected() is False and self.listener_thread_keep_running:
                        logger_comm_manager.warning("{} - MQTT not connected, waiting...".format(self.SECONDARY))
                        utime.sleep(1)
                    logger_comm_manager.info("Waiting for messages...")
                    self.conn_secondary.wait_msg()
                self.conn_secondary.close()
        self.listener_thread_joined = True

    def publisher_thread_func(self):
        st = utime.time()
        while self.publisher_thread_keep_running:
            logger_comm_manager.info("Waiting for message to send...")
            if not self.queue.empty():
                msg, pub_topic = self.queue.get()
                if self.en_conn_primary:
                    while not self.network_manager.is_ready() and self.publisher_thread_keep_running:
                        utime.sleep(1)
                    logger_comm_manager.info("Network ready, connecting to primary MQTT...")
                    self.conn_primary.connect()
                    while self.conn_primary.is_connected() is False and self.publisher_thread_keep_running:
                        logger_comm_manager.warning("{} - MQTT not connected, retrying...".format(self.PRIMARY))
                        utime.sleep(1)
                    topic = pub_topic if pub_topic is not None else wgps_configs.get_config("mqtt.primary.pub_topic")
                    logger_comm_manager.info("{} - Sending message on topic: {}".format(self.PRIMARY, topic))
                    self.publish_message(topic, msg, wgps_configs.get_config("mqtt.primary.qos"), self.conn_primary, wgps_configs.get_config("mqtt.primary.max_retries"), self.PRIMARY)
                    if self.status[self.PRIMARY] == self.READY:
                        logger_comm_manager.info("{} - Message sent.".format(self.PRIMARY))
                    else:
                        logger_comm_manager.error("{} - Message failed.".format(self.PRIMARY))
                    self.conn_primary.close()
                if not self.publisher_thread_keep_running:
                    break
                if self.en_conn_secondary:
                    while not self.network_manager.is_ready() and self.publisher_thread_keep_running:
                        utime.sleep(1)
                    logger_comm_manager.info("Network ready, connecting to secondary MQTT...")
                    self.conn_secondary.connect()
                    while self.conn_secondary.is_connected() is False and self.publisher_thread_keep_running:
                        logger_comm_manager.warning("{} - MQTT not connected, retrying...".format(self.SECONDARY))
                        utime.sleep(1)
                    topic = pub_topic if pub_topic is not None else wgps_configs.get_config("mqtt.secondary.pub_topic")
                    logger_comm_manager.info("{} - Sending message on topic: {}".format(self.SECONDARY, topic))
                    self.publish_message(topic, msg, wgps_configs.get_config("mqtt.secondary.qos"), self.conn_secondary, wgps_configs.get_config("mqtt.secondary.max_retries"), self.SECONDARY)
                    if self.status[self.SECONDARY] == self.READY:
                        logger_comm_manager.info("{} - Message sent.".format(self.SECONDARY))
                    else:
                        logger_comm_manager.error("{} - Message failed.".format(self.SECONDARY))
                    self.conn_secondary.close()
                st = utime.time()
            else:
                if utime.time() - st >= 3:
                    break
                utime.sleep(1)
        logger_comm_manager.info("Publisher thread exiting...")
        self.publisher_thread_joined = True

    def publish_message(self, topic, msg, qos, connection, max_retries, type):
        retries = 0
        while retries < max_retries:
            if connection.publish(topic, msg, qos=qos):
                self.status[type] = self.READY
                return
            else:
                logger_comm_manager.warning("MQTT publish failed, retrying... ({}/{})".format(retries + 1, max_retries))
                retries += 1
                utime.sleep(2)
        logger_comm_manager.error("Failed to publish message after {} retries".format(max_retries))
        self.status[type] = self.ERROR


# ===== FROM FILE: network_manager.py =====
logger_network_manager = log.getLogger("WGPS::NetworkManager")


class NetworkManager(Manager):

    def __init__(self):
        super().__init__()
        net.setModemFun(0)
        select_pin = wgps_configs.get_config("system.sim.select_pin")
        self.sim_manager = SimController(select_pin)
        self.sim_info = {}
        self.network_info = {"cellid": 0, "mcc": 0, "mnc": 0, "lac": 0}
        self.thread = None
        self.network_ready = False
        if len(wgps_configs.get_config("app.sim1.id")) == 0 or len(wgps_configs.get_config("app.sim2.id")) == 0:
            net.setModemFun(4)
            utime.sleep_ms(200)
            if self.sim_manager.get_sim_status()["sim_status"] == 1:
                self.sim_info["sim1"] = self.sim_manager.get_sim_info()
            net.setModemFun(0)
            utime.sleep_ms(200)
            self.sim_manager.switch_sim()
            net.setModemFun(4)
            utime.sleep_ms(200)
            if self.sim_manager.get_sim_status()["sim_status"] == 1:
                self.sim_info["sim2"] = self.sim_manager.get_sim_info()
            net.setModemFun(0)
            utime.sleep_ms(200)
            self.sim_manager.switch_sim()
            wgps_configs.set_config("app.sim1.id", self.sim_info.get("sim1", {}).get("sim_id", ""), False)
            wgps_configs.set_config("app.sim2.id", self.sim_info.get("sim2", {}).get("sim_id", ""), False)
            wgps_configs.save_all_configs()

    def start(self):
        if self.thread is not None:
            logger_network_manager.warning("NetworkManager thread already running")
            return
        self.keep_running = True
        self.thread_joined = False
        self.thread = _thread.start_new_thread(self.thread_func, ())
        logger_network_manager.info("NetworkManager thread started")

    def stop(self):
        self.keep_running = False
        if self.thread is not None:
            st = utime.time()
            while not self.thread_joined and utime.time() - st < 3:
                utime.sleep(1)
            if not self.thread_joined:
                _thread.stop_thread(self.thread)
            self.thread = None
            gc.collect()
            logger_network_manager.info("NetworkManager thread stopped")
        else:
            logger_network_manager.warning("NetworkManager thread is not running")

    def get_data(self):
        return self.network_info

    def get_sim_info(self):
        return self.sim_info

    def check_network_ready(self):
        stage, state = checkNet.waitNetworkReady(wgps_configs.get_config("network.timeout"))
        if stage == 3 and state == 1:
            logger_network_manager.info("Network connected!")
            self.network_ready = True
        else:
            logger_network_manager.error("Network connection failed: stage {}, state {}".format(stage, state))
            self.network_ready = False

    def collect_network_info(self):
        cell_info = net.getCellInfo()
        if cell_info != -1:
            if isinstance(cell_info, tuple) and len(cell_info) > 2 and isinstance(cell_info[2], list) and cell_info[2]:
                s = cell_info[2][0]
                self.network_info["cellid"] = int(s[1])
                self.network_info["mcc"] = int(s[2])
                self.network_info["mnc"] = int(s[3])
                self.network_info["lac"] = int(s[4])
        else:
            logger_network_manager.error("Failed to get cell info!")

    def init_network_connection(self, sim_number):
        net.setModemFun(1)
        sim_status = self.sim_manager.get_sim_status()["sim_status"]
        if sim_status != 1:
            logger_network_manager.error("SIM {} error: {}".format(sim_number, sim_status))
        else:
            logger_network_manager.info("SIM {} OK".format(sim_number))
            self.check_network_ready()

    def connect_network(self, sim_number):
        self.init_network_connection(sim_number)
        if self.network_ready:
            utime.sleep(2)
            st = utime.time()
            while self.keep_running and utime.time() - st < 10:
                self.collect_network_info()
                if any((value == 0 for value in self.network_info.values())):
                    logger_network_manager.warning("Some network info values are zero: {}".format(self.network_info))
                    utime.sleep(2)
                else:
                    self.ready = True
                    break
            if self.ready:
                logger_network_manager.info("Network connected on SIM 1")
                return True
            else:
                logger_network_manager.error("Failed to get valid network info on SIM 1: {}".format(self.network_info))
                return False
        else:
            logger_network_manager.error("Network connection failed on SIM 1")
            return False

    def sim_toggle(self):
        net.setModemFun(0)
        self.sim_manager.switch_sim()
        utime.sleep(2)

    def thread_func(self):
        net.setConfig(6)
        utime.sleep(1)
        net_mode, roaming_en = net.getConfig()
        logger_network_manager.info("Network mode: {}".format(net_mode))
        while self.keep_running:
            logger_network_manager.info("Connecting with SIM 1")
            if self.connect_network(1):
                break
            self.sim_toggle()
            logger_network_manager.info("Switched to SIM 2")
            logger_network_manager.info("Connecting with SIM 2")
            if self.connect_network(2):
                break
            self.sim_toggle()
            logger_network_manager.info("Switched to SIM 1")
        self.thread_joined = True

    def loop(self):
        pass


# ===== FROM FILE: nrf_manager.py =====
logger_nrf_manager = log.getLogger("WGPS::NRFManager")
NRF_INTERRUPTS = {1: "TMAG1", 2: "TMAG2", 4: "LDR", 8: "SHT30", 16: "DS3231", 32: "LSM6DSL"}


class NRFManager:
    NRF_I2C_INTR_REG = 1

    def __init__(self, interrupt_handler):
        self.interrupt_handler = interrupt_handler
        self.status_output = Pin(wgps_configs.get_config("system.nrf.status_pin"), Pin.OUT, Pin.PULL_DISABLE, 1)
        self.ext_intr = ExtInt(wgps_configs.get_config("system.nrf.extint_pin"), ExtInt.IRQ_RISING, ExtInt.PULL_DISABLE, self.default_handler)
        self.nrf_controller = NRFController(wgps_configs.get_config("system.nrf.i2c_channel"), wgps_configs.get_config("system.nrf.i2c_address"), wgps_configs.get_config("system.nrf.ble_en_pin"))
        logger_nrf_manager.info("NRFManager initialized.")

    def default_handler(self, args):
        if args[0] == wgps_configs.get_config("system.nrf.extint_pin") and args[1] == 0:
            status = self.nrf_controller.get_status()
            flags = self.nrf_controller.parse_sensor_flags(status)
            if any(flags.values()):
                self.interrupt_handler.handle_interrupt(flags, is_wakeup=False)
            self.nrf_controller.clear_sensor_flags()
            self.nrf_controller.request_sleep()

    def enable_interrupts(self):
        self.ext_intr.enable()

    def disable_interrupts(self):
        self.ext_intr.disable()

    def get_interrupt_status(self):
        status = self.nrf_controller.get_status()
        return self.nrf_controller.parse_sensor_flags(status)

    def set_status_output(self, level):
        try:
            self.status_output.write(level)
            return True
        except Exception as e:
            logger_nrf_manager.error("Failed to set NRF status output: %s", e)
            return False

    def nrf_is_awake(self):
        if self.ext_intr.read_level() == 1:
            return True
        return False

    def nrf_wakeup(self):
        try:
            self.nrf_controller.wakeup_nrf()
        except Exception as e:
            logger_nrf_manager.error("Failed to wake up NRF: %s", e)

    def nrf_clear_interrupts(self):
        try:
            self.nrf_controller.clear_sensor_flags()
        except Exception as e:
            logger_nrf_manager.error("Failed to clear NRF interrupts: %s", e)

    def nrf_sleep(self):
        try:
            self.nrf_controller.request_sleep()
            st = utime.time()
            while utime.time() - st < 2:
                if self.ext_intr.read_level() == 1:
                    return True
                utime.sleep_ms(100)
            utime.sleep_ms(100)
            logger_nrf_manager.warning("NRF did not respond to sleep request in time.")
            return False
        except Exception as e:
            logger_nrf_manager.error("Failed to put NRF to sleep: %s", e)
            return False

    def enable_sensor_interrupts(self):
        self.nrf_controller.disable_interrupts(disable_tmag1=False, disable_tmag2=False, disable_ldr=False, disable_sht30=False, disable_ds3231=False, disable_lsm6dsl=False)

    def enable_mag_switch_interrupt(self):
        self.nrf_controller.disable_interrupts(disable_tmag1=False, disable_tmag2=True, disable_ldr=True, disable_sht30=True, disable_ds3231=True, disable_lsm6dsl=True)
        logger_nrf_manager.info("Magnetic switch interrupt enabled.")

    def enable_tamper_interrupt(self):
        self.nrf_controller.disable_interrupts(disable_tmag1=True, disable_tmag2=False, disable_ldr=False, disable_sht30=True, disable_ds3231=True, disable_lsm6dsl=True)
        logger_nrf_manager.info("Magnetic tamper interrupt enabled.")

    def disable_sensor_interrupts(self):
        self.nrf_controller.disable_interrupts(disable_tmag1=True, disable_tmag2=True, disable_ldr=True, disable_sht30=True, disable_ds3231=True, disable_lsm6dsl=True)

    def ble_connect(self, addr):
        pass

    def ble_is_connected(self):
        pass

    def ble_disconnect(self):
        pass

    def ble_read_char(self, char_handle):
        pass

    def ble_write_char(self, char_handle, data):
        pass

    def ble_get_notification(self, char_handle, timeout):
        pass


# ===== FROM FILE: sensor_manager.py =====
logger_sensor_manager = log.getLogger("WGPS::SensorManager")


class SensorData(GenericData):

    def __init__(self):
        super().__init__()


class SensorManager(Manager):

    def __init__(self):
        super().__init__()
        self.battery = Battery(wgps_configs.get_config("system.battery.adc_period"), wgps_configs.get_config("system.battery.channel"), wgps_configs.get_config("system.battery.factor"), wgps_configs.get_config("system.battery.min_voltage"), wgps_configs.get_config("system.battery.max_voltage"))
        self.tmag_tamper = TMAG5273(wgps_configs.get_config("system.tmag_tamper.i2c_channel"), wgps_configs.get_config("system.tmag_tamper.address"))
        self.tmag_switch = TMAG5273(wgps_configs.get_config("system.tmag_switch.i2c_channel"), wgps_configs.get_config("system.tmag_switch.address"))
        self.data = {"battery_level": 0, "battery_voltage": 0.0, "temperature": 0, "humidity": 0}
        self.thread = None

    def start(self):
        if self.thread is not None:
            logger_sensor_manager.warning("SensorManager thread already running")
            return
        self.keep_running = True
        self.thread_joined = False
        self.thread = _thread.start_new_thread(self.thread_func, ())

    def stop(self):
        self.keep_running = False
        if self.thread is not None:
            st = utime.time()
            while not self.thread_joined and utime.time() - st < 3:
                utime.sleep_ms(1000)
            if not self.thread_joined:
                _thread.stop_thread(self.thread)
            self.thread = None
            gc.collect()

    def is_ready(self):
        return self.ready

    def get_data(self):
        return self.data

    def thread_func(self):
        self.read_battery_level()
        self.ready = True
        self.thread_joined = True

    def loop(self):
        pass

    def read_battery_level(self):
        battery_level, batt_v = self.battery.get_battery_level()
        if battery_level is not None:
            self.data["battery_level"] = round(battery_level, 2)
            self.data["battery_voltage"] = round(batt_v, 3)
        else:
            logger_sensor_manager.error("Failed to read battery level!")

    def setup_mag_tamper_mode(self, active):
        if not self.tmag_tamper.check_device():
            return False
        if active:
            self.tmag_tamper.set_mode("low_power")
            logger_sensor_manager.warning("TMAG Tamper set to low power mode, did you want interrupt?")
        else:
            self.tmag_tamper.set_mode("sleep")
            logger_sensor_manager.info("TMAG Tamper set to sleep mode.")
        return True

    def setup_mag_tamper_interrupt(self):
        if not self.tmag_tamper.check_device():
            return False
        if self.tmag_tamper.check_device():
            self.tmag_tamper.set_mode("active")
            self.tmag_tamper.set_mag_range("low")
            if wgps_configs.get_config("system.tmag_tamper.enabled"):
                self.tmag_tamper.configure_interrupt(wgps_configs.get_config("system.tmag_tamper.thresholds"), wgps_configs.get_config("system.tmag_tamper.alert"), wgps_configs.get_config("system.tmag_tamper.bidirectional"))
            logger_sensor_manager.info("Magnetic tamper interrupt configured.")
            return True
        return False

    def clear_mag_tamper_interrupt(self):
        if not self.tmag_tamper.check_device():
            return False
        if self.tmag_tamper.check_device():
            self.tmag_tamper.clear_interrupt()
            logger_sensor_manager.info("Magnetic tamper interrupt cleared.")
            return True
        return False

    def setup_mag_tamper_factory_levels(self):
        if not self.tmag_tamper.check_device():
            return False
        self.tmag_tamper.check_device()
        self.tmag_tamper.set_mag_axis_enable(True, True, True)
        self.tmag_tamper.set_mode("active")
        self.tmag_tamper.set_mag_range("low")
        vals = self.tmag_tamper.get_mag_values()
        logger_sensor_manager.info("TMAG Tamper Factory Levels - X: {}, Y: {}, Z: {}".format(vals[0], vals[1], vals[2]))
        wgps_configs.set_config("system.tmag_tamper.factory_levels", {"x": vals[0], "y": vals[1], "z": vals[2]})
        return True

    def setup_mag_tamper_thresholds(self):
        if not self.tmag_tamper.check_device():
            return False
        self.tmag_tamper.check_device()
        self.tmag_tamper.set_mag_axis_enable(True, True, True)
        self.tmag_tamper.set_mode("active")
        self.tmag_tamper.set_mag_range("low")
        vals = self.tmag_tamper.get_mag_values()
        factory_levels = wgps_configs.get_config("system.tmag_tamper.factory_levels")
        if factory_levels["z"] < -1.04 or factory_levels["z"] > 1.04:
            z_thr = factory_levels["z"]
        elif factory_levels["z"] >= 0:
            z_thr = 1.04
        else:
            z_thr = -1.04
        thresholds = {"x": 0, "y": 0, "z": z_thr}
        alert_mode = "over" if vals[2] < z_thr else "under"
        wgps_configs.set_config("system.tmag_tamper.thresholds", thresholds, save=False)
        wgps_configs.set_config("system.tmag_tamper.alert", alert_mode, save=False)
        wgps_configs.set_config("system.tmag_tamper.enabled", True, save=False)
        wgps_configs.save_all_configs()
        return True

    def get_tmag_tamper_temperature(self):
        if not self.tmag_tamper.check_device():
            return None
        self.tmag_tamper.check_device()
        self.tmag_tamper.enable_temperature()
        self.tmag_tamper.set_mode("active")
        utime.sleep_ms(100)
        temp = self.tmag_tamper.get_temperature()
        logger_sensor_manager.info("TMAG Tamper Temperature: {:.2f} °C".format(temp))
        return temp

    def setup_mag_switch_mode(self, active):
        if not self.tmag_switch.check_device():
            return False
        if active:
            self.tmag_switch.set_mode("low_power")
            logger_sensor_manager.warning("TMAG Switch set to low power mode, did you want interrupt?")
        else:
            self.tmag_switch.set_mode("sleep")
            logger_sensor_manager.info("TMAG Switch set to sleep mode.")
        return True

    def setup_mag_switch_interrupt(self):
        if not self.tmag_switch.check_device():
            return False
        if self.tmag_switch.check_device():
            self.tmag_switch.set_mode("active")
            self.tmag_switch.set_mag_range("low")
            if wgps_configs.get_config("system.tmag_switch.enabled"):
                self.tmag_switch.configure_interrupt(wgps_configs.get_config("system.tmag_switch.thresholds"), wgps_configs.get_config("system.tmag_switch.alert"), wgps_configs.get_config("system.tmag_switch.bidirectional"))
            logger_sensor_manager.info("Magnetic switch interrupt configured.")
            return True
        return False

    def clear_mag_switch_interrupt(self):
        if not self.tmag_switch.check_device():
            return False
        if self.tmag_switch.check_device():
            self.tmag_switch.clear_interrupt()
            logger_sensor_manager.info("Magnetic switch interrupt cleared.")
            return True
        return False

    def setup_mag_switch_thresholds(self):
        if not self.tmag_switch.check_device():
            return False
        self.tmag_switch.check_device()
        self.tmag_switch.set_mag_axis_enable(True, True, True)
        self.tmag_switch.set_mode("active")
        self.tmag_switch.set_mag_range("low")
        vals = self.tmag_switch.get_mag_values()
        deviation = wgps_configs.get_config("system.tmag_switch.deviation")
        thresholds = {"x": 0, "y": vals[1] + deviation, "z": 0}
        wgps_configs.set_config("system.tmag_switch.thresholds", thresholds, save=False)
        wgps_configs.set_config("system.tmag_switch.enabled", True, save=False)
        wgps_configs.save_all_configs()
        return True


# ===== FROM FILE: nvstack.py =====
logger_nvstack = log.getLogger("WGPS::NVStack")


class NVStackData:

    def __init__(self):
        self.data = {"packet_data": None, "hash": 0}
        self.filename = None

    def set_packet_data(self, packet_data: PacketData, filename):
        self.data["packet_data"] = packet_data
        self.data["hash"] = 0
        self.data["hash"] = self.generate_hash()
        self.filename = filename

    def get_packet_data(self):
        return self.data["packet_data"]

    def load_from_storage(self, filename):
        self.filename = filename
        try:
            file_path = "/".join([wgps_configs.get_config("system.storage.root"), self.filename])
            if self.data["packet_data"] is None:
                self.data["packet_data"] = PacketData()
            with open(file_path, "r") as fp:
                tmp_dict = ujson.load(fp)
                self.data["packet_data"].set_data(tmp_dict["packet_data"])
                self.data["hash"] = tmp_dict["hash"]
            if not self.validate_hash():
                logger_nvstack.error("Data validation failed, resetting data")
                self.data = {"packet_data": None, "hash": 0}
                return False
            logger_nvstack.info("Data loaded from {}".format(file_path))
            return True
        except OSError as e:
            logger_nvstack.error("Failed to load data from {}: {}".format(self.filename, e))
            self.data = {"packet_data": None, "hash": 0}
            return False

    def save_to_storage(self):
        if self.filename is None or len(self.filename) == 0:
            logger_nvstack.error("Filename not set, cannot save data")
            return False
        try:
            file_path = "/".join([wgps_configs.get_config("system.storage.root"), self.filename])
            with open(file_path, "w") as fp:
                tmp_dict = {"packet_data": self.data["packet_data"].get_data(), "hash": self.data["hash"]}
                ujson.dump(tmp_dict, fp)
            logger_nvstack.info("Data saved to {}".format(file_path))
            return True
        except OSError as e:
            logger_nvstack.error("Failed to save data to {}: {}".format(self.filename, e))
            return False

    def remove_from_storage(self):
        if self.filename is None or len(self.filename) == 0:
            logger_nvstack.error("Filename not set, cannot remove data")
            return False
        try:
            file_path = "/".join([wgps_configs.get_config("system.storage.root"), self.filename])
            uos.remove(file_path)
            logger_nvstack.info("Data removed from {}".format(file_path))
            return True
        except OSError as e:
            logger_nvstack.error("Failed to remove data from {}: {}".format(self.filename, e))
            return False

    def generate_hash(self):
        md5 = uhashlib.md5()
        tmp_dict = {"packet_data": self.data["packet_data"].get_data(), "hash": self.data["hash"]}
        md5.update(ujson.dumps(tmp_dict).encode("utf-8"))
        res = md5.digest()
        return ubinascii.hexlify(res).decode("utf-8")

    def validate_hash(self):
        if "hash" not in self.data:
            return False
        data_hash = self.data["hash"]
        self.data["hash"] = 0
        calculated_hash = self.generate_hash()
        self.data["hash"] = data_hash
        return data_hash == calculated_hash


class NVStack:
    MAX_COUNT = 10**21 - 1

    def __init__(self, nvstack_filename="nvstack.dat", counter_filename="counter.dat"):
        self.nvstack_filepath = "/".join([wgps_configs.get_config("system.storage.root"), nvstack_filename])
        self.counter_filepath = "/".join([wgps_configs.get_config("system.storage.root"), counter_filename])

    def update_counter(self):
        try:
            with open(self.counter_filepath, "r+b") as fp:
                data = fp.read(21)
                if not data:
                    count = 0
                else:
                    count = int(data.decode("utf-8").strip())
                if count >= self.MAX_COUNT:
                    count = 0
                else:
                    count += 1
                fp.seek(0)
                fp.write("{:021d}".format(count).encode("utf-8"))
            logger_nvstack.info("Counter updated to {}".format(count))
            return count
        except OSError:
            with open(self.counter_filepath, "wb") as fp:
                fp.write("{:021d}".format(0).encode("utf-8"))
            logger_nvstack.info("Counter file created with initial value 0")
            return 0

    def get_counter(self):
        try:
            with open(self.counter_filepath, "r") as fp:
                data = fp.read(21)
                if not data:
                    return 0
                return int(data.strip())
        except OSError:
            with open(self.counter_filepath, "wb") as fp:
                fp.write("{:021d}".format(0).encode("utf-8"))
            logger_nvstack.info("Counter file created with initial value 0")
            return 0

    def create_filename(self, prefix="data_", extension=".json"):
        count = self.get_counter()
        filename = "{}{:021d}{}".format(prefix, count, extension)
        return filename

    def push(self):
        filename = self.create_filename()
        wdata = ustruct.pack("31s", filename.encode("utf-8")[:31]) + b"\n"
        try:
            with open(self.nvstack_filepath, "ab") as fp:
                fp.write(wdata)
            logger_nvstack.info("Successfully wrote to NVStack file: {}".format(self.nvstack_filepath))
            self.update_counter()
            return True
        except Exception as e:
            logger_nvstack.error("Failed to write to NVStack file '{}': {}".format(self.nvstack_filepath, e))
            return False

    def peek(self):
        try:
            with open(self.nvstack_filepath, "rb") as fp:
                fp.seek(-32, 2)
                data = fp.read(32)
                if not data:
                    return None
                filename = ustruct.unpack("31s", data)[0].decode("utf-8").strip()
                return filename
        except Exception as e:
            logger_nvstack.error("Failed to read from NVStack file '{}': {}".format(self.nvstack_filepath, e))
            return None

    def pop(self):
        try:
            with open(self.nvstack_filepath, "rb") as fp:
                fp.seek(0, 2)
                filesize = fp.tell()
                if filesize < 32:
                    return False
                fp.seek(0)
                bytes_to_copy = filesize - 32
                temp_filepath = self.nvstack_filepath + ".tmp"
                with open(temp_filepath, "wb") as temp_fp:
                    chunk_size = 4096
                    copied = 0
                    while copied < bytes_to_copy:
                        to_read = min(chunk_size, bytes_to_copy - copied)
                        chunk = fp.read(to_read)
                        if not chunk:
                            break
                        temp_fp.write(chunk)
                        copied += len(chunk)
            uos.remove(self.nvstack_filepath)
            uos.rename(temp_filepath, self.nvstack_filepath)
            return True
        except Exception as e:
            logger_nvstack.error("Failed to pop from NVStack file '{}': {}".format(self.nvstack_filepath, e))
            return False


# ===== FROM FILE: nvstack_manager.py =====
logger_nvstack_manager = log.getLogger("WGPS::NVStackManager")


class NVStackManager(Manager):

    def __init__(self):
        super().__init__()
        self.top_data = None
        self.empty = True
        self.nvstack = NVStack()
        self.sd_card = SDCard()

    def start(self):
        if self.sd_card.mount():
            top_filename = self.nvstack.peek()
            if top_filename is not None:
                self.top_data = NVStackData()
                if self.top_data.load_from_storage(top_filename):
                    self.empty = False
                    logger_nvstack_manager.info("Loaded top data from {}".format(top_filename))
                else:
                    logger_nvstack_manager.error("Failed to load top data from storage")
            else:
                logger_nvstack_manager.info("No top data found in NVStack")
            self.ready = True
        else:
            logger_nvstack_manager.error("Failed to mount SD card, NVStackManager not ready")
            self.ready = False

    def stop(self):
        self.sd_card.unmount()
        self.ready = False

    def is_ready(self):
        return self.ready

    def get_data(self):
        pass

    def loop(self):
        pass

    def push_packet(self, packet: PacketData):
        ret = False
        if not self.ready:
            return False
        if self.top_data is None:
            self.top_data = NVStackData()
        filename = self.nvstack.create_filename()
        state = packet.get_state()
        packet.update_state(PacketData.HISTORY)
        self.top_data.set_packet_data(packet, filename)
        if self.top_data.save_to_storage():
            if self.nvstack.push():
                self.empty = False
                logger_nvstack_manager.info("Packet pushed to NVStack with filename {}".format(filename))
                ret = True
            else:
                self.top_data.remove_from_storage()
                logger_nvstack_manager.error("Failed to add stack entry")
        else:
            logger_nvstack_manager.error("Failed to save packet data to storage")
        packet.update_state(state)
        self.top_data.set_packet_data(packet, filename)
        return ret

    def is_empty(self):
        return self.empty

    def peek_packet(self):
        if not self.ready:
            return None
        if self.empty or self.top_data is None:
            return None
        return self.top_data.get_packet_data()

    def pop_packet(self):
        if not self.ready:
            return None
        if self.empty or self.top_data is None or self.top_data.filename is None:
            logger_nvstack_manager.error("No top data available to pop")
            return None
        packet = self.top_data.get_packet_data()
        self.nvstack.pop()
        self.top_data.remove_from_storage()
        while True:
            filename = self.nvstack.peek()
            if filename is not None:
                if self.top_data.load_from_storage(filename):
                    logger_nvstack_manager.info("Loaded new top data from {}".format(filename))
                    return packet
                else:
                    logger_nvstack_manager.error("Failed to load new top data from storage")
                    self.nvstack.pop()
            else:
                logger_nvstack_manager.info("No more data in NVStack")
                break
        self.top_data = None
        self.empty = True
        return packet


# ===== FROM FILE: wgps_operations.py =====
logger_wgps_operations = log.getLogger("WGPS::Operations")


class WGPS_Operation:

    def __init__(self, nrf_init=False, sensor_init=False, comm_init=False, location_init=False, sys_diag_init=False, nvstack_init=False, mqtt_handler_init=False, rtc: RTCController = None):
        self.running = False
        self.stop_requested = False
        self.shutdown_requested = False
        if mqtt_handler_init:
            self.queue = Queue(4)
        else:
            self.queue = None
        self.intr_list = []
        self.intr_handler = None
        self.nrf_manager = None
        self.sensor_manager = None
        self.network_manager = None
        self.comm_manager = None
        self.location_manager = None
        self.sys_diag_manager = None
        self.nvstack_manager = None
        self.rtc = rtc
        if nrf_init:
            self.nrf_manager_init()
        if sensor_init:
            self.sensor_manager_init()
        if comm_init:
            self.comm_manager_init()
        if location_init:
            self.location_manager_init()
        if sys_diag_init:
            self.sys_diag_manager_init()
        if nvstack_init:
            self.nvstack_manager_init()
        gc.collect()
        logger_wgps_operations.info("WGPS Operation Base Initialized")

    def nrf_manager_init(self):
        if self.nrf_manager is None:
            try:
                self.intr_list = list(NRF_INTERRUPTS.values())
                self.intr_handler = InterruptHandler(self.intr_list)
                self.nrf_manager = NRFManager(self.intr_handler)
            except Exception as e:
                logger_wgps_operations.error("Failed to initialize NRF manager: %s", e)
                self.nrf_manager = None
                self.intr_handler = None
                self.intr_list = []

    def sensor_manager_init(self):
        if self.sensor_manager is None:
            try:
                self.sensor_manager = SensorManager()
            except Exception as e:
                logger_wgps_operations.error("Failed to initialize Sensor manager: %s", e)
                self.sensor_manager = None

    def comm_manager_init(self):
        if self.comm_manager is None:
            try:
                if self.network_manager is None:
                    self.network_manager = NetworkManager()
                self.comm_manager = CommunicationManager(self.network_manager)
            except Exception as e:
                logger_wgps_operations.error("Failed to initialize Communication manager: %s", e)
                self.comm_manager = None

    def location_manager_init(self):
        if self.location_manager is None:
            try:
                self.location_manager = LocationManager()
            except Exception as e:
                logger_wgps_operations.error("Failed to initialize Location manager: %s", e)
                self.location_manager = None

    def sys_diag_manager_init(self):
        if self.sys_diag_manager is None:
            try:
                self.sys_diag_manager = SysDiagnosticsManager()
            except Exception as e:
                logger_wgps_operations.error("Failed to initialize SysDiagnostics manager: %s", e)
                self.sys_diag_manager = None

    def nvstack_manager_init(self):
        if self.nvstack_manager is None:
            try:
                self.nvstack_manager = NVStackManager()
            except Exception as e:
                logger_wgps_operations.error("Failed to initialize NVStack manager: %s", e)
                self.nvstack_manager = None

    def handle_mqtt_msg(self, topic, msg):
        logger_wgps_operations.info("Handling MQTT message in WGPS_Operation: Topic: %.16s Msg: %.16s...", topic, msg)
        if topic != wgps_configs.get_config("mqtt.secondary.sub_topic"):
            logger_wgps_operations.warning("MQTT topic does not match subscribed topic. Ignoring message.")
            return
        try:
            payload = ujson.loads(msg)
            req_id, command = self.check_command_request(payload)
            if req_id is None or command is None:
                return
            if command in ["stop", "sleep"]:
                self.stop()
            elif not self.queue.put(payload):
                logger_wgps_operations.error("Failed to enqueue MQTT message payload!")
        except Exception as e:
            logger_wgps_operations.error("Failed to process MQTT message: {}".format(e))

    def extract_command_header(self, payload):
        device_id = payload.get("device_id", None)
        req_id = payload.get("req_id", None)
        command = payload.get("command", None)
        return (device_id, req_id, command)

    def check_command_request(self, payload):
        device_id, req_id, command = self.extract_command_header(payload)
        if device_id != wgps_configs.get_config("app.device.id"):
            logger_wgps_operations.warning("MQTT message device_id does not match!")
            logger_wgps_operations.info("Expected: %s, Received: %s", wgps_configs.get_config("app.device.id"), device_id)
        if req_id is None or command is None:
            logger_wgps_operations.warning("MQTT message missing req_id or command. Ignoring message.")
            return (None, None)
        return (req_id, command)

    def make_response_message(self, device_id: str, req_id: str, command: str, status: bool, extra_fields={}):
        msg = {"device_id": device_id, "req_id": req_id, "command": command, "success": status}
        msg.update(extra_fields)
        return msg

    def __del__(self):
        if self.nrf_manager is not None:
            self.nrf_manager.set_status_output(0)

    def start(self):
        pass

    def is_running(self):
        return self.running

    def stop(self):
        if self.stop_requested:
            return
        self.stop_requested = True
        self.nrf_manager.nrf_wakeup()
        utime.sleep_ms(2000)
        self.nrf_manager.disable_sensor_interrupts()
        self.sensor_manager.clear_mag_switch_interrupt()
        self.sensor_manager.clear_mag_tamper_interrupt()
        if wgps_configs.get_config("system.state") == "normal":
            if wgps_configs.get_config("system.tmag_tamper.enabled"):
                self.nrf_manager.enable_tamper_interrupt()
                self.nrf_manager.nrf_sleep()
                self.sensor_manager.setup_mag_tamper_interrupt()
                self.sensor_manager.setup_mag_switch_mode(False)
            else:
                self.nrf_manager.nrf_sleep()
                self.sensor_manager.setup_mag_tamper_mode(False)
                self.sensor_manager.setup_mag_switch_mode(False)
        elif wgps_configs.get_config("system.state") == "factory":
            self.nrf_manager.enable_mag_switch_interrupt()
            self.nrf_manager.nrf_sleep()
            self.sensor_manager.setup_mag_switch_interrupt()
            self.sensor_manager.setup_mag_tamper_mode(False)
        else:
            self.nrf_manager.nrf_sleep()
            self.sensor_manager.setup_mag_switch_mode(False)
            self.sensor_manager.setup_mag_tamper_mode(False)
        self.running = False

    def shutdown(self):
        if self.shutdown_requested:
            return
        self.shutdown_requested = True
        self.keep_running = False

    def publish_packet(self, packet_json):
        if self.comm_manager.send_json(packet_json):
            while self.keep_running:
                if self.comm_manager.wait_until_finished(2):
                    send_status = self.comm_manager.get_status()
                    if send_status[CommunicationManager.PRIMARY] == CommunicationManager.READY and send_status[CommunicationManager.SECONDARY] == CommunicationManager.READY:
                        logger_wgps_operations.info("Packet sent successfully.")
                        return True
                    else:
                        logger_wgps_operations.error("Packet sending failed.")
        else:
            logger_wgps_operations.error("Failed to send packet via Comm Manager.")
        return False

    def process_nvstack(self):
        if self.nvstack_manager.is_empty():
            logger_wgps_operations.info("NVStack is empty, no packets to process.")
            return
        if wgps_configs.get_config("system.battery.level") < 10:
            logger_wgps_operations.warning("Battery level too low, skipping NVStack processing.")
            return
        if not self.nvstack_manager.is_ready():
            logger_wgps_operations.error("NVStack Manager not ready, cannot process NVStack.")
            return
        while not self.nvstack_manager.is_empty():
            comm_status = self.comm_manager.get_status()
            if comm_status[CommunicationManager.PRIMARY] == CommunicationManager.READY and comm_status[CommunicationManager.SECONDARY] == CommunicationManager.READY:
                packet = self.nvstack_manager.peek_packet()
                if packet is None:
                    break
                packet.update_state(PacketData.HISTORY)
                if self.publish_packet(packet.get_data()):
                    self.nvstack_manager.pop_packet()
                else:
                    break
        self.nvstack_manager.stop()

    def publish_wakeup_message(self, state):
        try:
            extra_fields = {"wake_reason": self.wkup_reason, "firmware_version": get_firmware_version(), "state": str(state)}
            msg = self.make_response_message(wgps_configs.get_config("app.device.id"), "{}".format(utime.ticks_ms()), "wakeup", True, extra_fields)
            self.comm_manager.send_json(msg, response=True)
            logger_wgps_operations.info("Published wakeup MQTT message.")
            self.wakeup_msg_published = True
        except Exception as e:
            logger_wgps_operations.error("Failed to publish wakeup MQTT message: {}".format(e))

    def handle_set_recovery_command(self, params, device_id, req_id, command):
        logger_wgps_operations.info("Handling set_recovery command.")
        try:
            enable = params.get("enable", None)
            if not isinstance(enable, bool):
                raise Exception("Invalid enable parameter; must be boolean")
            if enable:
                wgps_configs.set_config("system.state", "recovery")
                self.update_command_string("RecoveryEnable", self.rtc.get_time_string())
            else:
                wgps_configs.set_config("system.state", "normal")
                self.update_command_string("RecoveryDisable", self.rtc.get_time_string())
            msg = self.make_response_message(device_id, req_id, command, True, {"state": wgps_configs.get_config("system.state")})
        except Exception as e:
            logger_wgps_operations.error("Failed to handle set_recovery command: %s", e)
            msg = self.make_response_message(device_id, req_id, command, False, {"reason": str(e)})
        try:
            self.comm_manager.send_json(msg, response=True)
        except Exception as e:
            logger_wgps_operations.error("Failed to send set_recovery response: %s", e)
        logger_wgps_operations.info("set_recovery command processing completed.")

    def update_command_string(self, command, time_str):
        cmd_str = "{}@{}".format(command, time_str)
        cmd_logs = wgps_configs.get_config("statistics.cmd_logs")
        cmd_logs.append(cmd_str)
        wgps_configs.set_config("statistics.cmd_logs", cmd_logs)


class WGPS_OpFirstBoot(WGPS_Operation):

    def __init__(self, rtc: RTCController):
        super().__init__(nrf_init=True, sensor_init=True, rtc=rtc)

    def start(self):
        self.running = True
        self.execute()

    def execute(self):
        logger_wgps_operations.info("Executing OpFirstBoot operation.")
        self.sensor_manager.setup_mag_switch_thresholds()
        imei = get_device_imei()
        wgps_configs.set_config("app.device.imei", "{}".format(imei), False)
        wgps_configs.set_config("app.device.id", "{}".format(imei), False)
        wgps_configs.set_config("mqtt.secondary.client_id", "{}".format(imei), False)
        wgps_configs.set_config("mqtt.secondary.sub_topic", "irwgps/{}/init".format(imei), False)
        wgps_configs.set_config("system.state", "factory", False)
        wgps_configs.save_all_configs()
        self.nrf_manager.nrf_wakeup()
        utime.sleep_ms(2000)
        self.nrf_manager.disable_sensor_interrupts()
        self.nrf_manager.enable_mag_switch_interrupt()
        self.nrf_manager.nrf_sleep()
        self.sensor_manager.setup_mag_switch_interrupt()
        self.sensor_manager.setup_mag_tamper_mode(False)
        self.running = False


class WGPS_OpFactory(WGPS_Operation):

    def __init__(self, rtc: RTCController):
        super().__init__(nrf_init=True, sensor_init=True, comm_init=True, mqtt_handler_init=True, rtc=rtc)
        self.keep_running = False
        self.thread = None

    def start(self):
        if self.thread is not None:
            logger_wgps_operations.warning("OpFactory thread already running")
            return
        self.running = True
        self.keep_running = True
        self.thread = _thread.start_new_thread(self.execute, ())

    def execute(self):
        logger_wgps_operations.info("Executing OpFactory operation.")
        self.nrf_manager.disable_sensor_interrupts()
        self.nrf_manager.nrf_sleep()
        self.sensor_manager.setup_mag_switch_mode(False)
        self.sensor_manager.setup_mag_tamper_mode(False)
        self.clear_sdcard()
        logger_wgps_operations.info("Starting Net & Comm Managers...")
        self.network_manager.start()
        self.comm_manager.init_connections(False, True, cb=self.handle_mqtt_msg)
        self.comm_manager.start()
        logger_wgps_operations.info("Net & Comm Managers Started.")
        gc.collect()
        logger_wgps_operations.info("Mem free: {}".format(gc.mem_free()))
        start = utime.ticks_ms()
        timeout = wgps_configs.get_config("ops.factory.cycle_timeout_s") * 1000
        while self.keep_running and utime.ticks_diff(utime.ticks_ms(), start) < timeout:
            if self.queue.empty():
                utime.sleep_ms(500)
                continue
            payload = self.queue.get()
            if payload is not None:
                start = utime.ticks_ms()
                device_id, req_id, command = self.extract_command_header(payload)
                if command == "initialize":
                    if self.initialize(payload.get("configs", {}), device_id, req_id, command):
                        utime.sleep(5)
                        break
                if command == "calibrate":
                    self.calibrate(device_id, req_id, command)
                if command == "send_config" or command == "query_config":
                    self.send_configs(device_id, req_id, command)
                if command == "activate":
                    if self.activate(payload.get("activation_info", {}), device_id, req_id, command):
                        utime.sleep(5)
                        break
            gc.collect()
            logger_wgps_operations.info("Mem free: {}".format(gc.mem_free()))
        self.comm_manager.stop()
        self.network_manager.stop()
        self.stop()

    def initialize(self, configs, device_id, req_id, command):
        logger_wgps_operations.info("Initializing in OpFactory")
        ret = False
        try:
            logger_wgps_operations.info("Received configs for initialization: {}".format(configs))
            if wgps_configs.get_config("app.device.imei") != configs["app"]["device"]["imei"]:
                raise Exception("Device IMEI does not match!")
            sim_info = self.network_manager.get_sim_info()
            sim1_iccid = sim_info.get("sim1", {}).get("iccid", "")
            sim2_iccid = sim_info.get("sim2", {}).get("iccid", "")
            if configs["app"]["sim1"]["id"] != sim1_iccid:
                raise Exception("SIM1 ICCID does not match!")
            if configs["app"]["sim2"]["id"] != sim2_iccid:
                raise Exception("SIM2 ICCID does not match!")
            if configs["system"]["state"] != "factory":
                raise Exception("Can not change the sate in initialize!")
            if not wgps_configs.merge_configs(configs, False):
                raise Exception("Failed to merge provided configs")
            wgps_configs.set_config("system.state", "factory", False)
            wgps_configs.set_config("system.tmag_switch.enabled", True, False)
            wgps_configs.set_config("system.tmag_tamper.enabled", False, False)
            wgps_configs.save_all_configs()
            self.sensor_manager.setup_mag_switch_thresholds()
            msg = self.make_response_message(device_id, req_id, command, True, {"configs": wgps_configs.configs})
            ret = True
            logger_wgps_operations.info("Updated configs in initialization: {}".format(configs))
        except Exception as e:
            logger_wgps_operations.error("Failed to initialize configs: {}".format(e))
            msg = self.make_response_message(device_id, req_id, command, False, {"reason": str(e)})
        self.comm_manager.send_json(msg, response=True)
        logger_wgps_operations.info("Initialization in OpFactory completed.")
        return ret

    def calibrate(self, device_id, req_id, command):
        logger_wgps_operations.info("Calibrating in OpFactory operation.")
        try:
            self.sensor_manager.setup_mag_tamper_factory_levels()
            self.sensor_manager.setup_mag_tamper_mode(False)
            msg = self.make_response_message(device_id, req_id, command, True, {"factory_levels": wgps_configs.get_config("system.tmag_tamper.factory_levels")})
        except Exception as e:
            logger_wgps_operations.error("Failed to calibrate TMAG tamper sensor: {}".format(e))
            msg = self.make_response_message(device_id, req_id, command, False, {"reason": str(e)})
        self.comm_manager.send_json(msg, response=True)
        logger_wgps_operations.info("Calibration in OpFactory completed.")

    def validate_activation_info(self, activation_info):
        try:
            if not isinstance(activation_info, dict):
                logger_wgps_operations.error("Activation info is not a dict")
                return False
            required = ["device_id", "primary_broker", "port", "ssl_enabled", "username", "password", "primary.pub_topic", "secondary.pub_topic", "secondary.sub_topic", "secondary.resp_topic"]
            for f in required:
                if f not in activation_info:
                    logger_wgps_operations.error("Activation info missing required field: %s", f)
                    return False
                if activation_info[f] is None:
                    logger_wgps_operations.error("Activation info field %s is None", f)
                    return False
            if not isinstance(activation_info.get("device_id"), str) or not activation_info.get("device_id").strip():
                logger_wgps_operations.error("Invalid device_id in activation info")
                return False
            if "wagon_id" in activation_info and activation_info.get("wagon_id") is not None:
                if not isinstance(activation_info.get("wagon_id"), str):
                    logger_wgps_operations.error("Invalid wagon_id in activation info; must be string")
                    return False
            if not isinstance(activation_info.get("primary_broker"), str) or not activation_info.get("primary_broker").strip():
                logger_wgps_operations.error("Invalid primary_broker in activation info")
                return False
            port = activation_info.get("port")
            if not isinstance(port, int):
                try:
                    port = int(port)
                except Exception:
                    logger_wgps_operations.error("Invalid port in activation info; must be integer")
                    return False
            if port < 1 or port > 65535:
                logger_wgps_operations.error("Port out of valid range: %s", port)
                return False
            if not isinstance(activation_info.get("ssl_enabled"), bool):
                logger_wgps_operations.error("Invalid ssl_enabled in activation info; must be bool")
                return False
            for s in ("username", "password"):
                v = activation_info.get(s)
                if not isinstance(v, str) or not v:
                    logger_wgps_operations.error("Invalid %s in activation info", s)
                    return False
            for topic_key in ("primary.pub_topic", "secondary.pub_topic", "secondary.sub_topic", "secondary.resp_topic"):
                topic = activation_info.get(topic_key)
                if not isinstance(topic, str) or not topic.strip():
                    logger_wgps_operations.error("Invalid %s in activation info", topic_key)
                    return False
            return True
        except Exception as e:
            logger_wgps_operations.error("Exception while validating activation info: %s", e)
            return False

    def activate(self, activation_info, device_id, req_id, command):
        logger_wgps_operations.info("Activating in OpFactory operation")
        ret = False
        try:
            if self.validate_activation_info(activation_info):
                logger_wgps_operations.info("Activation info validated successfully.")
                resp_topic = wgps_configs.get_config("mqtt.secondary.resp_topic")
                wgps_configs.create_backup()
                wgps_configs.set_config("app.device.id", activation_info["device_id"], False)
                wgps_configs.set_config("mqtt.primary.broker", activation_info["primary_broker"], False)
                wgps_configs.set_config("mqtt.primary.port", activation_info["port"], False)
                wgps_configs.set_config("mqtt.primary.ssl_en", activation_info["ssl_enabled"], False)
                wgps_configs.set_config("mqtt.primary.username", activation_info["username"], False)
                wgps_configs.set_config("mqtt.primary.password", activation_info["password"], False)
                wgps_configs.set_config("mqtt.primary.pub_topic", activation_info["primary.pub_topic"], False)
                wgps_configs.set_config("mqtt.secondary.pub_topic", activation_info["secondary.pub_topic"], False)
                wgps_configs.set_config("mqtt.secondary.sub_topic", activation_info["secondary.sub_topic"], False)
                wgps_configs.set_config("mqtt.secondary.resp_topic", activation_info["secondary.resp_topic"], False)
                if "client_key" in activation_info and "client_cert" in activation_info:
                    certs_dir = wgps_configs.get_config("system.certs.primary")
                    cert_file = "{}/{}".format(certs_dir, activation_info["client_key"]["filename"])
                    key_file = "{}/{}".format(certs_dir, activation_info["client_cert"]["filename"])
                    with open(key_file, "w") as f:
                        f.write(activation_info["client_key"]["data"])
                    with open(cert_file, "w") as f:
                        f.write(activation_info["client_cert"]["data"])
                    wgps_configs.set_config("mqtt.primary.cert_file", cert_file, False)
                    wgps_configs.set_config("mqtt.primary.key_file", key_file, False)
                if "wagon_id" in activation_info:
                    wgps_configs.set_config("app.device.wagon_id", activation_info["wagon_id"], False)
                wgps_configs.set_config("system.state", "normal", False)
                wgps_configs.set_config("system.tmag_switch.enabled", False, False)
                wgps_configs.set_config("system.tmag_tamper.enabled", True, False)
                wgps_configs.save_all_configs()
                self.sensor_manager.setup_mag_tamper_thresholds()
            else:
                logger_wgps_operations.error("Activation info validation failed.")
                raise Exception("Invalid activation info provided")
            msg = self.make_response_message(device_id, req_id, command, True, {"configs": wgps_configs.configs})
            ret = True
        except Exception as e:
            logger_wgps_operations.error("Failed to activate: {}".format(e))
            msg = self.make_response_message(device_id, req_id, command, False, {"reason": str(e)})
        self.comm_manager.send_json(msg, response=True, pub_topic=resp_topic)
        logger_wgps_operations.info("Activation in OpFactory completed.")
        return ret

    def send_configs(self, device_id, req_id, command):
        logger_wgps_operations.info("Sending configs in OpFactory operation.")
        try:
            msg = self.make_response_message(device_id, req_id, command, True, {"configs": wgps_configs.configs})
        except Exception as e:
            logger_wgps_operations.error("Failed to send configs: {}".format(e))
            msg = self.make_response_message(device_id, req_id, command, False, {"reason": str(e)})
        self.comm_manager.send_json(msg, response=True)
        logger_wgps_operations.info("Config send in OpFactory completed.")

    def clear_sdcard(self):
        sd_controller = SDCard()
        if sd_controller.mount():
            sd_controller.format()
            sd_controller.unmount()
            logger_wgps_operations.info("SD card cleared!")


class WGPS_OpNormal(WGPS_Operation):

    def __init__(self, rtc: RTCController):
        super().__init__(nrf_init=True, sensor_init=True, comm_init=True, location_init=True, nvstack_init=True, mqtt_handler_init=True, rtc=rtc)
        self.wakeup_msg_published = False
        self.keep_running = False
        self.thread = None

    def start(self):
        if self.thread is not None:
            logger_wgps_operations.warning("OpNormal thread already running")
            return
        self.running = True
        self.keep_running = True
        self.wkup_reason = power_ctrl.get_power_on_reason()
        logger_wgps_operations.info("WGPS OpNormal Wakeup Reason: %s", self.wkup_reason)
        self.thread = _thread.start_new_thread(self.execute, ())

    def execute(self):
        logger_wgps_operations.info("Executing OpNormal operation.")
        tamper = False
        if self.wkup_reason == power_ctrl.POWER_ON_REASON_PWRKEY:
            tamper = self.check_tamper()
        tempc = self.sensor_manager.get_tmag_tamper_temperature()
        self.sensor_manager.setup_mag_switch_mode(False)
        self.sensor_manager.setup_mag_tamper_interrupt()
        if tempc is not None and tempc > wgps_configs.get_config("system.sht30.temp_high"):
            logger_wgps_operations.warning("Device temperature too high ({} °C)!".format(tempc))
        else:
            self.network_manager.start()
            self.comm_manager.init_connections(True, True, cb=self.handle_mqtt_msg)
            self.comm_manager.start()
            self.location_manager.start()
            self.sensor_manager.start()
            pkt_builder = PacketBuilder()
            packet_num = wgps_configs.get_config("statistics.cnt") + 1
            pkt_builder.update_control_commands_data(wgps_configs.get_config("statistics.cmd_logs"))
            pkt_builder.update_device_data(get_firmware_version(), wgps_configs.get_config("app.device.id"), "L", packet_num, wgps_configs.get_config("app.device.imei"))
            pkt_builder.update_alerts_data(tamper, False)
            pkt_builder.update_time_data_transmission(get_startup_time_str())
            pkt_builder.update_wagon_status_data(False)
            wgps_configs.set_config("statistics.cnt", packet_num, False)
            wgps_configs.set_config("statistics.cmd_logs", [], False)
            wgps_configs.save_all_configs()
            start = utime.ticks_ms()
            timeout = wgps_configs.get_config("ops.normal.cycle_timeout_s") * 1000
            while self.keep_running and utime.ticks_diff(utime.ticks_ms(), start) < timeout and (len(pkt_builder.get_pending_reqs()) > 0):
                if self.comm_manager.is_ready() and self.wakeup_msg_published is False:
                    self.publish_wakeup_message("normal")
                if self.network_manager.is_ready() and pkt_builder.is_req_gsm_data():
                    try:
                        network_data = self.network_manager.get_data()
                        pkt_builder.update_gsm_data(network_data["mcc"], network_data["mnc"], network_data["lac"], network_data["cellid"])
                    except Exception as e:
                        logger_wgps_operations.error("Failed to update GSM data in pkt_builder: %s", e)
                if self.location_manager.is_ready():
                    if not (pkt_builder.is_req_fixation_data() or pkt_builder.is_req_time_data_gnss() or pkt_builder.is_req_gnss_details_data() or pkt_builder.is_req_accuracy_data()):
                        self.location_manager.stop()
                    else:
                        try:
                            location_data = self.location_manager.get_data()
                            if pkt_builder.is_req_fixation_data():
                                pkt_builder.update_fixation_data(location_data["fix"], location_data["lat"], location_data["latd"], location_data["lng"], location_data["lngd"], location_data["alt"])
                            if pkt_builder.is_req_time_data_gnss():
                                pkt_builder.update_time_data_gnss(location_data["dtpf"])
                                self.rtc.set_datetime_from_gnss(location_data["dtpf"], location_data["dtpf_set"])
                            if pkt_builder.is_req_gnss_details_data():
                                pkt_builder.update_gnss_details_data(location_data["nos"], location_data["spd"], location_data["navst"], location_data["sv"])
                            if pkt_builder.is_req_accuracy_data():
                                pkt_builder.update_accuracy_data(location_data["hdop"], location_data["pdop"])
                        except Exception as e:
                            logger_wgps_operations.error("Failed to update location data in pkt_builder: %s", e)
                if self.sensor_manager.is_ready() and pkt_builder.is_req_device_health_data():
                    try:
                        sensor_data = self.sensor_manager.get_data()
                        pkt_builder.update_device_health_data(sensor_data["battery_level"], tempc)
                        wgps_configs.set_config("system.battery.level", sensor_data["battery_level"], False)
                        wgps_configs.set_config("system.battery.voltage", sensor_data["battery_voltage"], False)
                        wgps_configs.save_all_configs()
                        self.sensor_manager.stop()
                    except Exception as e:
                        logger_wgps_operations.error("Failed to update sensor data in pkt_builder: %s", e)
                utime.sleep_ms(1000)
            if len(pkt_builder.get_pending_reqs()) > 0:
                logger_wgps_operations.warning("Could not collect: %s", pkt_builder.get_pending_reqs())
            self.location_manager.stop()
            self.sensor_manager.stop()
            packet_json, packet_dict = pkt_builder.build_packet_json()
            logger_wgps_operations.info("Built packet:\n%s\n", packet_json)
            self.nvstack_manager.start()
            nv_stack_st = utime.time()
            while not self.nvstack_manager.is_ready() and utime.time() - nv_stack_st < 3:
                utime.sleep_ms(250)
            data_packet = PacketData()
            data_packet.set_data(packet_dict)
            if self.nvstack_manager.is_ready():
                pushed = False
                if self.nvstack_manager.push_packet(data_packet):
                    pushed = True
                if pushed and self.publish_packet(packet_dict):
                    self.nvstack_manager.pop_packet()
                self.process_nvstack()
            else:
                logger_wgps_operations.error("NVStack Manager not ready; cannot store packet, attempting send...")
                self.publish_packet(packet_dict)
            self.process_command_queue()
            self.comm_manager.stop()
            self.network_manager.stop()
        if tamper:
            wgps_configs.set_config("system.state", "recovery")
        self.stop()

    def check_tamper(self):
        self.nrf_manager.nrf_wakeup()
        utime.sleep_ms(2000)
        tamper_status = self.nrf_manager.get_interrupt_status()
        self.nrf_manager.nrf_clear_interrupts()
        self.nrf_manager.nrf_sleep()
        if tamper_status.get("TMAG2", False) or tamper_status.get("LDR", False):
            logger_wgps_operations.warning("Tamper detected via NRF interrupts: %s", tamper_status)
            return True
        logger_wgps_operations.info("No tamper detected via NRF interrupts.")
        return False

    def process_command_queue(self):
        if self.queue.empty():
            return
        logger_wgps_operations.info("Processing command queue in OpNormal.")
        utime.sleep(4)
        while self.keep_running:
            if self.queue.empty():
                break
            payload = self.queue.get()
            if payload is not None:
                device_id, req_id, command = self.extract_command_header(payload)
                if command == "firmware_update":
                    self.handle_firmware_update_command(payload.get("params", {}), device_id, req_id, command)
                elif command == "deactivate":
                    self.handle_deactivate_command(device_id, req_id, command)
                    break
                elif command == "set_recovery":
                    self.handle_set_recovery_command(payload.get("params", {}), device_id, req_id, command)
                    break
                elif command == "set_ble":
                    self.handle_set_ble_command(payload, device_id, req_id, command)

    def handle_firmware_update_command(self, payload, device_id, req_id, command):
        logger_wgps_operations.info("Handling firmware_update command.")
        try:
            fota_manager = FotaManager(app_name=get_project_name(), app_version=get_firmware_version(), dev_id=wgps_configs.get_config("app.device.id"), dev_imei=get_device_imei())
            if fota_manager.verify_fota_request(payload):
                msg = self.make_response_message(device_id, req_id, command, True, {})
            else:
                raise Exception("Invalid FOTA request")
            fota_manager.handle_fota_request(payload)
            self.update_command_string("FirmwareUpdate", self.rtc.get_time_string())
            self.comm_manager.send_json(msg, response=True)
            fota_manager.complete_fota()
        except Exception as e:
            logger_wgps_operations.error("Failed to handle firmware_update command: {}".format(e))
            msg = self.make_response_message(device_id, req_id, command, False, {"reason": str(e)})
            self.comm_manager.send_json(msg, response=True)

    def handle_deactivate_command(self, device_id, req_id, command):
        logger_wgps_operations.info("Handling deactivate command in OpNormal.")
        pub_topic = wgps_configs.get_config("mqtt.secondary.resp_topic")
        try:
            wgps_configs.restore_backup()
            self.update_command_string("Deactivate", self.rtc.get_time_string())
            msg = self.make_response_message(device_id, req_id, command, True, {"configs": wgps_configs.configs})
        except Exception as e:
            logger_wgps_operations.error("Failed to deactivate: %s", e)
            msg = self.make_response_message(device_id, req_id, command, False, {"reason": str(e)})
        try:
            self.comm_manager.send_json(msg, response=True, pub_topic=pub_topic)
        except Exception as e:
            logger_wgps_operations.error("Failed to send deactivate response: %s", e)
        logger_wgps_operations.info("Device set to factory mode and activation configs rolled back.")

    def handle_set_ble_command(self, payload, device_id, req_id, command):
        logger_wgps_operations.info("Handling set_ble command in OpNormal.")
        try:
            enable = payload.get("params", {}).get("enable", None)
            if not isinstance(enable, bool):
                raise Exception("Invalid enable parameter; must be boolean")
            wgps_configs.set_config("ble.enabled", enable)
            wgps_configs.save_all_configs()
            cmd = "BLEEnable" if enable else "BLEDisable"
            self.update_command_string(cmd, self.rtc.get_time_string())
            msg = self.make_response_message(device_id, req_id, command, True, {"enabled": wgps_configs.get_config("ble.enabled")})
        except Exception as e:
            logger_wgps_operations.error("Failed to handle set_ble command: %s", e)
            msg = self.make_response_message(device_id, req_id, command, False, {"reason": str(e)})
        try:
            self.comm_manager.send_json(msg, response=True)
        except Exception as e:
            logger_wgps_operations.error("Failed to send set_ble response: %s", e)
        logger_wgps_operations.info("set_ble command processing completed.")


class WGPS_OpRecovery(WGPS_Operation):

    def __init__(self, rtc: RTCController):
        super().__init__(nrf_init=True, sensor_init=True, comm_init=True, location_init=True, nvstack_init=True, mqtt_handler_init=True, rtc=rtc)
        self.wakeup_msg_published = False
        self.keep_running = False
        self.thread = None

    def start(self):
        if self.thread is not None:
            logger_wgps_operations.warning("OpRecovery thread already running")
            return
        self.running = True
        self.keep_running = True
        self.wkup_reason = power_ctrl.get_power_on_reason()
        logger_wgps_operations.info("WGPS OpRecovery Wakeup Reason: %s", self.wkup_reason)
        self.thread = _thread.start_new_thread(self.execute, ())

    def execute(self):
        logger_wgps_operations.info("Executing OpRecovery operation.")
        self.nrf_manager.nrf_wakeup()
        utime.sleep_ms(2000)
        self.nrf_manager.disable_sensor_interrupts()
        self.nrf_manager.nrf_sleep()
        tempc = self.sensor_manager.get_tmag_tamper_temperature()
        self.sensor_manager.setup_mag_switch_mode(False)
        self.sensor_manager.setup_mag_tamper_mode(False)
        if tempc is not None and tempc > wgps_configs.get_config("system.sht30.temp_high"):
            logger_wgps_operations.warning("Device temperature too high ({} °C)!".format(tempc))
        else:
            self.network_manager.start()
            self.comm_manager.init_connections(True, True, cb=self.handle_mqtt_msg)
            self.comm_manager.start()
            self.location_manager.start()
            self.sensor_manager.start()
            pkt_builder = PacketBuilder()
            packet_num = wgps_configs.get_config("statistics.cnt") + 1
            pkt_builder.update_control_commands_data(wgps_configs.get_config("statistics.cmd_logs"))
            pkt_builder.update_device_data(get_firmware_version(), wgps_configs.get_config("app.device.id"), "L", packet_num, wgps_configs.get_config("app.device.imei"))
            pkt_builder.update_alerts_data(True, True)
            pkt_builder.update_time_data_transmission(get_startup_time_str())
            pkt_builder.update_wagon_status_data(False)
            wgps_configs.set_config("statistics.cnt", packet_num, False)
            wgps_configs.set_config("statistics.cmd_logs", [], False)
            wgps_configs.save_all_configs()
            start = utime.ticks_ms()
            timeout = wgps_configs.get_config("ops.normal.cycle_timeout_s") * 1000
            while self.keep_running and utime.ticks_diff(utime.ticks_ms(), start) < timeout and (len(pkt_builder.get_pending_reqs()) > 0):
                if self.comm_manager.is_ready() and self.wakeup_msg_published is False:
                    self.publish_wakeup_message("recovery")
                if self.network_manager.is_ready() and pkt_builder.is_req_gsm_data():
                    try:
                        network_data = self.network_manager.get_data()
                        pkt_builder.update_gsm_data(network_data["mcc"], network_data["mnc"], network_data["lac"], network_data["cellid"])
                    except Exception as e:
                        logger_wgps_operations.error("Failed to update GSM data in pkt_builder: %s", e)
                if self.location_manager.is_ready():
                    if not (pkt_builder.is_req_fixation_data() or pkt_builder.is_req_time_data_gnss() or pkt_builder.is_req_gnss_details_data() or pkt_builder.is_req_accuracy_data()):
                        self.location_manager.stop()
                    else:
                        try:
                            location_data = self.location_manager.get_data()
                            if pkt_builder.is_req_fixation_data():
                                pkt_builder.update_fixation_data(location_data["fix"], location_data["lat"], location_data["latd"], location_data["lng"], location_data["lngd"], location_data["alt"])
                            if pkt_builder.is_req_time_data_gnss():
                                pkt_builder.update_time_data_gnss(location_data["dtpf"])
                                self.rtc.set_datetime_from_gnss(location_data["dtpf"], location_data["dtpf_set"])
                            if pkt_builder.is_req_gnss_details_data():
                                pkt_builder.update_gnss_details_data(location_data["nos"], location_data["spd"], location_data["navst"], location_data["sv"])
                            if pkt_builder.is_req_accuracy_data():
                                pkt_builder.update_accuracy_data(location_data["hdop"], location_data["pdop"])
                        except Exception as e:
                            logger_wgps_operations.error("Failed to update location data in pkt_builder: %s", e)
                if self.sensor_manager.is_ready() and pkt_builder.is_req_device_health_data():
                    try:
                        sensor_data = self.sensor_manager.get_data()
                        pkt_builder.update_device_health_data(sensor_data["battery_level"], tempc)
                        wgps_configs.set_config("system.battery.level", sensor_data["battery_level"], False)
                        wgps_configs.set_config("system.battery.voltage", sensor_data["battery_voltage"], False)
                        wgps_configs.save_all_configs()
                        self.sensor_manager.stop()
                    except Exception as e:
                        logger_wgps_operations.error("Failed to update sensor data in pkt_builder: %s", e)
                utime.sleep_ms(1000)
            if len(pkt_builder.get_pending_reqs()) > 0:
                logger_wgps_operations.warning("Could not collect: %s", pkt_builder.get_pending_reqs())
            self.location_manager.stop()
            self.sensor_manager.stop()
            packet_json, packet_dict = pkt_builder.build_packet_json()
            logger_wgps_operations.info("Built packet:\n%s\n", packet_json)
            self.nvstack_manager.start()
            nv_stack_st = utime.time()
            while not self.nvstack_manager.is_ready() and utime.time() - nv_stack_st < 3:
                utime.sleep_ms(250)
            data_packet = PacketData()
            data_packet.set_data(packet_dict)
            if self.nvstack_manager.is_ready():
                pushed = False
                if self.nvstack_manager.push_packet(data_packet):
                    pushed = True
                if pushed and self.publish_packet(packet_dict):
                    self.nvstack_manager.pop_packet()
                self.process_nvstack()
            else:
                logger_wgps_operations.error("NVStack Manager not ready; cannot store/send packet")
                self.publish_packet(packet_dict)
            self.process_command_queue()
            self.comm_manager.stop()
            self.network_manager.stop()
        self.stop()

    def process_command_queue(self):
        if self.queue.empty():
            return
        logger_wgps_operations.info("Processing command queue in OpNormal.")
        utime.sleep(4)
        while self.keep_running:
            if self.queue.empty():
                break
            payload = self.queue.get()
            if payload is not None:
                device_id, req_id, command = self.extract_command_header(payload)
                if command == "set_recovery":
                    self.handle_set_recovery_command(payload.get("params", {}), device_id, req_id, command)
                    break


# ===== ENTRY FILE: main.py =====
sys.path.append("/usr")
gc.enable()
PROJECT_NAME = "WGPS"
PROJECT_VERSION = "1.0.0"
logger_main = log.getLogger("WGPS::Main")


class WGPS_App:

    def __init__(self):
        logger_main.info("Initializing WGPS Application...")
        self.rtc_ctrl = RTCController()
        self.setup_configs()
        self.setup_time()
        self.setup_startup_time()
        set_firmware_version(PROJECT_VERSION)
        set_project_name(PROJECT_NAME)
        self.setup_power_basic()
        self.timer = Timer(Timer.Timer1)
        self.application = None
        self.keep_running = False
        logger_main.info("WGPS Application Initialized")

    def setup_configs(self):
        if not wgps_configs.load_configs():
            logger_main.error("Failed to load WGPS configurations")
            return
        logger_main.info("WGPS Configurations Loaded Successfully")

    def setup_time(self):
        if wgps_configs.get_config("system.rtc.enabled"):
            self.ext_rtc_hw = DS3231(wgps_configs.get_config("system.rtc.channel"), wgps_configs.get_config("system.rtc.address"))
        else:
            self.ext_rtc_hw = None
        self.rtc_ctrl.setup(self.ext_rtc_hw)
        logger_main.info("WGPS Time Setup Completed")

    def setup_startup_time(self):
        state = wgps_configs.get_config("system.state")
        interval = wgps_configs.get_config("ops.{}.periodic_interval_s".format(state))
        current_time = self.rtc_ctrl.get_time()
        curr_start_ts = utime.mktime((current_time[0], current_time[1], current_time[2], current_time[3], current_time[4], current_time[5], 0, 0))
        last_start_ts = int(wgps_configs.get_config("statistics.last_startup_ts"))
        if last_start_ts != 0:
            diff = curr_start_ts - (last_start_ts + interval)
            logger_main.info("Time since last startup: {} seconds".format(diff))
            if diff >= -180 and diff <= 180:
                curr_start_ts = last_start_ts + interval
                tmp = utime.localtime(curr_start_ts)
                current_time = (tmp[0], tmp[1], tmp[2], tmp[3], tmp[4], tmp[5])
        wgps_configs.set_config("statistics.last_startup_ts", curr_start_ts)
        time_format = str(wgps_configs.get_config("time.time_format"))
        time_str = time_format.format(current_time[0], current_time[1], current_time[2], current_time[3], current_time[4], current_time[5], wgps_configs.get_config("time.time_zone_offset"))
        set_startup_time(time_str, current_time)

    def setup_power_basic(self):
        global power_ctrl
        power_ctrl.set_autosleep(True)

    def app_start(self):
        logger_main.info("WGPS Application Starting...")
        self.clear_rtc_wakeup()
        state = wgps_configs.get_config("system.state")
        logger_main.info("Current system state: %s", state)
        self.keep_running = True
        self.app_monitor_start(state)
        if state == "first_boot":
            logger_main.info("First boot detected. Running initial setup...")
            self.application = WGPS_OpFirstBoot(self.rtc_ctrl)
            self.application.start()
            self.wait_for_app_completion()
            utime.sleep(10)
            logger_main.info("First boot operations complete. Powering off...")
        elif state == "factory":
            logger_main.info("Factory mode detected. Running factory operations...")
            self.application = WGPS_OpFactory(self.rtc_ctrl)
            self.application.start()
            self.wait_for_app_completion()
            logger_main.info("Factory operations complete. Powering off...")
        elif state == "normal":
            logger_main.info("Normal operation mode detected. Running normal operations...")
            self.application = WGPS_OpNormal(self.rtc_ctrl)
            self.application.start()
            self.wait_for_app_completion()
            if wgps_configs.get_config("system.state") == "recovery":
                self.app_monitor_stop()
                power_ctrl.restart()
            else:
                logger_main.info("Normal operations complete. Powering off...")
        elif state == "recovery":
            logger_main.info("Recovery mode detected. Running recovery operations...")
            self.application = WGPS_OpRecovery(self.rtc_ctrl)
            self.application.start()
            self.wait_for_app_completion()
            logger_main.info("Recovery operations complete. Powering off...")
        if wgps_configs.get_config("system.state") in ["normal", "recovery"]:
            self.set_rtc_wakeup()
        self.app_monitor_stop()
        logger_main.info("WGPS Application Shutting Down...")
        power_ctrl.power_off()

    def wait_for_app_completion(self):
        while self.application.is_running() and self.keep_running:
            utime.sleep(1)
        if self.application.is_running():
            self.application.shutdown()
        while self.application.is_running():
            utime.sleep(1)

    def app_monitor_start(self, state):
        try:
            self.timer.start(period=(wgps_configs.get_config("ops.{}.cycle_timeout_s".format(state)) + 60) * 1000, mode=Timer.ONE_SHOT, callback=self.app_monitor_cb)
            logger_main.info("WGPS Application Monitor Armed")
        except Exception as e:
            logger_main.error("Error starting application monitor: {}".format(e))

    def app_monitor_stop(self):
        try:
            self.timer.stop()
            logger_main.info("WGPS Application Monitor Disarmed")
        except Exception as e:
            logger_main.error("Error stopping application monitor: {}".format(e))

    def app_monitor_cb(self, args):
        self.keep_running = False
        logger_main.warning("WGPS Application Monitor Triggered - Stopping Application")

    def set_rtc_wakeup(self):
        state = wgps_configs.get_config("system.state")
        start_time = get_startup_time()
        if state == "normal":
            timeout_s = wgps_configs.get_config("ops.normal.periodic_interval_s")
        elif state == "recovery":
            timeout_s = wgps_configs.get_config("ops.recovery.periodic_interval_s")
        else:
            timeout_s = 3600
        start_sec = utime.mktime((start_time[0], start_time[1], start_time[2], start_time[3], start_time[4], start_time[5], 0, 0))
        wakeup_sec = start_sec + timeout_s
        wakeup_time = utime.localtime(wakeup_sec)
        self.rtc_ctrl.set_alarm([wakeup_time[0], wakeup_time[1], wakeup_time[2], 0, wakeup_time[3], wakeup_time[4], wakeup_time[5], 0])
        logger_main.info("RTC Wakeup Set for {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(wakeup_time[0], wakeup_time[1], wakeup_time[2], wakeup_time[3], wakeup_time[4], wakeup_time[5]))

    def clear_rtc_wakeup(self):
        self.rtc_ctrl.clear_alarm()
        logger_main.info("RTC Wakeup Cleared")


if __name__ == "__main__":
    log.basicConfig(level=log.DEBUG)
    try:
        app = WGPS_App()
        app.app_start()
    except KeyboardInterrupt:
        logger_main.info("WGPS Application Interrupted by User")
        app.keep_running = False
        app.wait_for_app_completion()
    sys.exit(0)
