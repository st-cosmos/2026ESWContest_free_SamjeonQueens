#
# Smart shelf node - E73-2G4M08S1C (nRF52840) + CS1237 load cell ADC
#
cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(smart_shelf)

target_sources(app PRIVATE
  src/main.c
  src/cs1237.c
  src/shelf_cal.c
  src/shelf_coap.c
  src/shelf_mode.c
)

target_include_directories(app PRIVATE src)
