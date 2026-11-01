/* EVBro — Modal «Todos los sensores y cables».
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

function openAllSensorsModal() {
  const modal = document.getElementById('allSensorsModal');
  if (!modal) return;
  const tag = document.getElementById('sensorsVinTag');
  if (tag) tag.innerText = currentVin || 'Ebro Auto';
  modal.classList.add('open');
  renderAllSensorsList();
}

function closeAllSensorsModal() {
  const modal = document.getElementById('allSensorsModal');
  if (modal) modal.classList.remove('open');
}

function filterSensorsList() {
  const query = (document.getElementById('sensorSearchInput')?.value || '').toLowerCase().trim();
  const cat = document.getElementById('sensorCategoryFilter')?.value || 'all';
  renderAllSensorsList(query, cat);
}

function renderAllSensorsList(filterQuery = '', filterCategory = 'all') {
  const container = document.getElementById('allSensorsListContainer');
  const countBadge = document.getElementById('sensorsCountBadge');
  if (!container) return;

  const data = latestTelemetryData || {};
  const raw = data.raw_data || data.raw || {};
  const doors = data.doors || {};
  const windows = data.windows || {};
  const tpms = data.tyre_pressures || {};
  const tpmsKpa = data.tyre_pressures_kpa || {};
  const tpmsTemp = data.tyre_temps || data.tyre_temperatures || {};
  const tpmsWarn = data.tyre_warnings || {};
  const seats = data.seat_heating || {};
  const seatsVent = data.seat_ventilation || {};

  // A two-state reading. «Sin datos» when the car did not send it: never shown as closed or off (docs/frontend.md).
  // Tones: '' plain, 'on' something working (charging, heating), 'warn' something open, 'alert' a warning from the car.
  const flag = (v, yes, no, yesTone = '', noTone = '') => (v === null || v === undefined
    ? { value: 'Sin datos', tone: 'unknown' } : { value: v ? yes : no, tone: v ? yesTone : noTone });

  const items = [
    // 1. Cables and charging
    {
      category: 'charging',
      catName: 'Cables & Carga',
      name: 'Estado del Cable de Carga (Gun State)',
      key: 'chargeGunState',
      haEntity: 'binary_sensor.ebro_cable_conectado',
      ...flag(data.cable_connected, 'Conectado', 'Desconectado', 'on', ''),
      details: data.charge_gun_state_desc || (data.cable_connected ? 'Cable enchufado al vehículo' : 'Sin cable conectado')
    },
    {
      category: 'charging',
      catName: 'Cables & Carga',
      name: 'Toma Carga Rápida CCS2 / AC (Gun Status)',
      key: 'fastChargingGunStatus',
      haEntity: 'sensor.ebro_toma_carga_rapida',
      value: data.fast_charging_gun_desc || (data.fast_charging ? 'CCS2 Conectada' : 'Desconectada'),
      tone: data.fast_charging ? 'on' : '',
      details: `Valor crudo: ${raw.fastChargingGunStatus ?? (data.fast_charging ? 2 : 0)} (0=Libre, 1=AC, 2=DC CCS2)`
    },
    {
      category: 'charging',
      catName: 'Cables & Carga',
      name: 'Estado del Proceso de Carga',
      key: 'chargeState',
      haEntity: 'sensor.ebro_estado_de_carga',
      value: data.charge_state_desc || (data.is_charging ? 'Cargando activamente' : 'Reposo'),
      tone: data.is_charging ? 'on' : '',
      details: `Valor crudo: ${raw.chargeState ?? (data.is_charging ? 1 : 0)} (0=Reposo, 1=Cargando, 2=Completada)`
    },
    {
      category: 'charging',
      catName: 'Cables & Carga',
      name: 'Potencia de Carga en Vivo (kW)',
      key: 'charge_power_kw',
      haEntity: 'sensor.ebro_potencia_carga',
      value: data.charge_power_kw ? `${data.charge_power_kw} kW` : (data.is_charging ? 'Activa' : '0.0 kW'),
      tone: data.charge_power_kw > 0 ? 'on' : '',
      details: 'Cálculo dinámico: (Tensión × Intensidad) / 1000'
    },
    {
      category: 'charging',
      catName: 'Cables & Carga',
      name: 'Tensión Batería Alta Tensión (V)',
      key: 'totalVoltage',
      haEntity: 'sensor.ebro_voltaje_bateria_hv',
      value: data.battery_voltage ? `${Math.round(data.battery_voltage)} V` : (raw.totalVoltage ? `${raw.totalVoltage} V` : 'Sin datos'),
      statusClass: 'parked',
      details: 'Tensión del pack de tracción PHEV'
    },
    {
      category: 'charging',
      catName: 'Cables & Carga',
      name: 'Intensidad Batería Alta Tensión (A)',
      key: 'totalCurrent',
      haEntity: 'sensor.ebro_intensidad_bateria_hv',
      value: data.battery_current !== undefined && data.battery_current !== null ? `${data.battery_current} A` : (raw.totalCurrent !== undefined ? `${raw.totalCurrent} A` : 'Sin datos'),
      statusClass: 'parked',
      details: 'Corriente continua de tracción/recarga'
    },
    {
      category: 'charging',
      catName: 'Cables & Carga',
      name: 'Tiempo Restante para Carga Completa',
      key: 'remainChargeTime',
      haEntity: 'sensor.ebro_tiempo_restante_carga',
      value: data.remaining_charge_time_min ? `${data.remaining_charge_time_min} min` : 'No cargando',
      tone: data.remaining_charge_time_min ? 'on' : '',
      details: 'Estimación calculada por el BMS del vehículo'
    },
    {
      category: 'charging',
      catName: 'Cables & Carga',
      name: 'Carga Programada (Appointment Charge)',
      key: 'appointmentChargeState',
      haEntity: 'sensor.ebro_carga_programada',
      value: data.appointment_charge_desc || 'Desactivada',
      statusClass: 'parked',
      details: t('Valor crudo: {p0}', {p0: raw.appointmentChargeState ?? 0})
    },
    {
      category: 'charging',
      catName: 'Cables & Carga',
      name: 'Aviso Nivel Crítico Batería Tracción',
      key: 'socLowCall',
      haEntity: 'binary_sensor.ebro_bateria_baja',
      value: data.soc_low_alert || raw.socLowCall == 1 ? 'BATERÍA BAJA' : 'Normal',
      statusClass: (data.soc_low_alert || raw.socLowCall == 1) ? 'danger' : 'active',
      details: 'Alerta del BMS cuando el nivel de tracción es crítico'
    },

    // 2. Battery and PHEV ranges
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Nivel Batería Tracción (SOC)',
      key: 'dumpEnergy / soc',
      haEntity: 'sensor.ebro_bateria',
      value: data.battery_level !== null && data.battery_level !== undefined ? `${Math.round(data.battery_level)}%` : 'Sin datos',
      statusClass: (data.battery_level || 0) > 20 ? 'active' : 'danger',
      details: 'Capacidad útil restante de la batería de tracción'
    },
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Autonomía 100% Eléctrica (EV Range)',
      key: 'pureElectricRange',
      haEntity: 'sensor.ebro_autonomia_electrica',
      value: data.electric_range_km !== null && data.electric_range_km !== undefined ? `${Math.round(data.electric_range_km)} km` : 'Sin datos',
      statusClass: 'active',
      details: 'Alcance estimado en modo puro eléctrico EV'
    },
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Autonomía Gasolina / Térmica',
      key: 'mileageSurplus',
      haEntity: 'sensor.ebro_autonomia_gasolina',
      value: data.fuel_range_km !== null && data.fuel_range_km !== undefined ? `${Math.round(data.fuel_range_km)} km` : 'Sin datos',
      statusClass: 'parked',
      details: 'Alcance restante del depósito de gasolina'
    },
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Autonomía Total Combinada PHEV',
      key: 'pureElectricRange + mileageSurplus',
      haEntity: 'sensor.ebro_autonomia_total',
      value: data.total_range_km !== null && data.total_range_km !== undefined ? `${Math.round(data.total_range_km)} km` : 'Sin datos',
      statusClass: 'active',
      details: 'Suma de autonomía eléctrica + gasolina'
    },
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Nivel Depósito Gasolina (%)',
      key: 'oilSurplus',
      haEntity: 'sensor.ebro_nivel_deposito_gasolina',
      value: data.fuel_level !== null && data.fuel_level !== undefined ? `${Math.round(data.fuel_level)}%` : (raw.oilSurplus !== undefined ? `${raw.oilSurplus}%` : 'Sin datos'),
      statusClass: 'parked',
      details: 'Porcentaje de volumen de combustible restante en el depósito'
    },
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Odómetro Total del Vehículo',
      key: 'odometer',
      haEntity: 'sensor.ebro_odometro_total',
      value: data.odometer_km ? `${Math.round(data.odometer_km).toLocaleString()} km` : 'Sin datos',
      statusClass: 'parked',
      details: 'Kilómetros totales acumulados por el vehículo'
    },
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Odómetro Modo 100% Eléctrico',
      key: 'electricRange',
      haEntity: 'sensor.ebro_odometro_electrico',
      value: data.ev_odometer_km ? `${Math.round(data.ev_odometer_km).toLocaleString()} km` : (raw.electricRange ? `${Math.round(raw.electricRange).toLocaleString()} km` : 'Sin datos'),
      statusClass: 'active',
      details: 'Kilómetros circulados exclusivamente con el motor eléctrico'
    },
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Odómetro Modo Híbrido',
      key: 'hybridMileage',
      haEntity: 'sensor.ebro_odometro_hibrido',
      value: data.hybrid_odometer_km ? `${Math.round(data.hybrid_odometer_km).toLocaleString()} km` : (raw.hybridMileage ? `${Math.round(raw.hybridMileage).toLocaleString()} km` : 'Sin datos'),
      statusClass: 'parked',
      details: 'Kilómetros circulados en modo híbrido combinado'
    },
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Consumo Medio de Combustible',
      key: 'averageFuel',
      haEntity: 'sensor.ebro_consumo_medio_combustible',
      value: data.average_fuel_l100km !== null && data.average_fuel_l100km !== undefined ? `${data.average_fuel_l100km} L/100km` : (raw.averageFuel ? `${raw.averageFuel} L/100km` : 'Sin datos'),
      statusClass: 'parked',
      details: 'Consumo promedio histórico de combustible'
    },
    {
      category: 'battery',
      catName: 'Batería & Rangos',
      name: 'Consumo Eléctrico Medio Reciente (50 km)',
      key: 'avgHkPowerKwh50km',
      haEntity: 'sensor.ebro_consumo_electrico_50km',
      value: data.average_power_kwh100km !== null && data.average_power_kwh100km !== undefined ? `${data.average_power_kwh100km} kWh/100km` : (raw.avgHkPowerKwh50km ? `${raw.avgHkPowerKwh50km} kWh/100km` : 'Sin datos'),
      statusClass: 'parked',
      details: 'Eficiencia eléctrica en los últimos 50 km'
    },

    // 3. TPMS tyres
    {
      category: 'tpms',
      catName: 'Neumáticos (TPMS)',
      name: 'Neumático Delantero Izquierdo (FL)',
      key: 'lFrontTyreKpa / lFrontTyreTemp',
      haEntity: 'sensor.ebro_rueda_delantera_izquierda',
      value: t('{p0} bar · {p1} °C', {p0: tpms.fl || '--', p1: tpmsTemp.fl || '--'}),
      statusClass: tpmsWarn.fl ? 'danger' : 'active',
      details: t('Presión: {p0} kPa | Alerta: {p1}', {p0: tpmsKpa.fl || '--', p1: tpmsWarn.fl ? 'ALERTA TPMS' : 'Correcto'})
    },
    {
      category: 'tpms',
      catName: 'Neumáticos (TPMS)',
      name: 'Neumático Delantero Derecho (FR)',
      key: 'rFrontTyreKpa / rFrontTyreTemp',
      haEntity: 'sensor.ebro_rueda_delantera_derecha',
      value: t('{p0} bar · {p1} °C', {p0: tpms.fr || '--', p1: tpmsTemp.fr || '--'}),
      statusClass: tpmsWarn.fr ? 'danger' : 'active',
      details: t('Presión: {p0} kPa | Alerta: {p1}', {p0: tpmsKpa.fr || '--', p1: tpmsWarn.fr ? 'ALERTA TPMS' : 'Correcto'})
    },
    {
      category: 'tpms',
      catName: 'Neumáticos (TPMS)',
      name: 'Neumático Trasero Izquierdo (RL)',
      key: 'lRearTyreKpa / lRearTyreTemp',
      haEntity: 'sensor.ebro_rueda_trasera_izquierda',
      value: t('{p0} bar · {p1} °C', {p0: tpms.rl || '--', p1: tpmsTemp.rl || '--'}),
      statusClass: tpmsWarn.rl ? 'danger' : 'active',
      details: t('Presión: {p0} kPa | Alerta: {p1}', {p0: tpmsKpa.rl || '--', p1: tpmsWarn.rl ? 'ALERTA TPMS' : 'Correcto'})
    },
    {
      category: 'tpms',
      catName: 'Neumáticos (TPMS)',
      name: 'Neumático Trasero Derecho (RR)',
      key: 'rRearTyreKpa / rRearTyreTemp',
      haEntity: 'sensor.ebro_rueda_trasera_derecha',
      value: t('{p0} bar · {p1} °C', {p0: tpms.rr || '--', p1: tpmsTemp.rr || '--'}),
      statusClass: tpmsWarn.rr ? 'danger' : 'active',
      details: t('Presión: {p0} kPa | Alerta: {p1}', {p0: tpmsKpa.rr || '--', p1: tpmsWarn.rr ? 'ALERTA TPMS' : 'Correcto'})
    },

    // 4. Body & locks
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Cierre Centralizado de Puertas',
      key: 'doorLock',
      haEntity: 'lock.ebro_cierre_centralizado',
      ...flag(data.is_locked, 'Bloqueado', 'Desbloqueado', '', 'warn'),
      details: `Valor crudo: ${raw.doorLock ?? (data.is_locked ? 0 : 1)} (0=Bloqueado, 1=Desbloqueado)`
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Puerta Delantera Izquierda (Conductor)',
      key: 'frontLeftDoor',
      haEntity: 'binary_sensor.ebro_puerta_conductor',
      ...flag(doors.fl, 'Abierta', 'Cerrada', 'warn', ''),
      details: 'Sensor magnético de puerta conductor'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Puerta Delantera Derecha (Acompañante)',
      key: 'frontRightDoor',
      haEntity: 'binary_sensor.ebro_puerta_pasajero',
      ...flag(doors.fr, 'Abierta', 'Cerrada', 'warn', ''),
      details: 'Sensor magnético de puerta copiloto'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Puerta Trasera Izquierda',
      key: 'backLeftDoor',
      haEntity: 'binary_sensor.ebro_puerta_trasera_izquierda',
      ...flag(doors.rl, 'Abierta', 'Cerrada', 'warn', ''),
      details: 'Sensor de puerta trasera izquierda'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Puerta Trasera Derecha',
      key: 'backRightDoor',
      haEntity: 'binary_sensor.ebro_puerta_trasera_derecha',
      ...flag(doors.rr, 'Abierta', 'Cerrada', 'warn', ''),
      details: 'Sensor de puerta trasera derecha'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Portón del Maletero (Trunk)',
      key: 'trunkDoor',
      haEntity: 'binary_sensor.ebro_maletero',
      ...flag(doors.trunk, 'Abierto', 'Cerrado', 'warn', ''),
      details: 'Estado de apertura del maletero'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Operación Portón Eléctrico',
      key: 'liftgateOperateState',
      haEntity: 'sensor.ebro_operacion_porton',
      value: data.liftgate_operate ? t('En Movimiento ({p0})', {p0: data.liftgate_operate}) : 'En Reposo',
      statusClass: 'parked',
      details: 'Estado dinámico del mecanismo del portón trasero'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Capó del Motor (Hood)',
      key: 'hood',
      haEntity: 'binary_sensor.ebro_capo',
      ...flag(doors.hood, 'Abierto', 'Cerrado', 'warn', ''),
      details: 'Estado de apertura del capó delantero'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Ventanilla Delantera Izquierda',
      key: 'frontLeftWindowState',
      haEntity: 'cover.ebro_ventanilla_conductor',
      ...flag(windows.fl, 'Abierta', 'Cerrada', 'warn', ''),
      details: 'Estado de la ventanilla del conductor'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Ventanilla Delantera Derecha',
      key: 'frontRightWindowState',
      haEntity: 'cover.ebro_ventanilla_pasajero',
      ...flag(windows.fr, 'Abierta', 'Cerrada', 'warn', ''),
      details: 'Estado de la ventanilla del pasajero'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Ventanilla Trasera Izquierda',
      key: 'backLeftWindowState',
      haEntity: 'cover.ebro_ventanilla_trasera_izquierda',
      ...flag(windows.rl, 'Abierta', 'Cerrada', 'warn', ''),
      details: 'Estado de la ventanilla trasera izquierda'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Ventanilla Trasera Derecha',
      key: 'backRightWindowState',
      haEntity: 'cover.ebro_ventanilla_trasera_derecha',
      ...flag(windows.rr, 'Abierta', 'Cerrada', 'warn', ''),
      details: 'Estado de la ventanilla trasera derecha'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Techo Solar Panorámico',
      key: 'sunroofState',
      haEntity: 'cover.ebro_techo_solar',
      ...flag(windows.sunroof, 'Abierto', 'Cerrado', 'warn', ''),
      details: 'Estado del cristal del techo solar'
    },
    {
      category: 'body',
      catName: 'Carrocería & Cierres',
      name: 'Cortinilla Parasol del Techo Solar',
      key: 'sunshadeState',
      haEntity: 'cover.ebro_cortinilla_techo',
      ...flag(windows.sunshade, 'Abierta', 'Cerrada', 'warn', ''),
      details: 'Persiana / parasol eléctrico del techo'
    },

    // 5. Climate & comfort
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Climatizador Frontal (HVAC)',
      key: 'frontHVACState',
      haEntity: 'climate.ebro_climatizador',
      ...flag(data.climate_on, 'Encendido', 'Apagado', 'on', ''),
      details: 'Sistema de climatización del habitáculo'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Temperatura Consigna Conductor (Izq)',
      key: 'frontSetTempLeft',
      haEntity: 'sensor.ebro_temperatura_objetivo_izq',
      value: data.target_temp_left ? `${data.target_temp_left}°C` : (data.target_temp ? `${data.target_temp}°C` : 'Sin datos'),
      statusClass: 'active',
      details: 'Temperatura seleccionada zona conductor'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Temperatura Consigna Pasajero (Der)',
      key: 'frontSetTempRight',
      haEntity: 'sensor.ebro_temperatura_objetivo_der',
      value: data.target_temp_right ? `${data.target_temp_right}°C` : (data.target_temp ? `${data.target_temp}°C` : 'Sin datos'),
      statusClass: 'active',
      details: 'Temperatura seleccionada zona copiloto'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Calefacción del Volante',
      key: 'steerWheelHeating',
      haEntity: 'switch.ebro_calefaccion_volante',
      ...flag(data.steer_wheel_heat, 'Activa', 'Apagada', 'on', ''),
      details: 'Calefactor eléctrico integrado en el aro del volante'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Desempañado Parabrisas Delantero',
      key: 'fWinHeatingState / frontWindshieldHeat',
      haEntity: 'switch.ebro_desempanado_delantero',
      ...flag(data.front_windshield_heat, 'Activo', 'Apagado', 'on', ''),
      details: 'Ventilación y resistencias luna delantera'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Luneta Térmica Trasera',
      key: 'rWinHeatingState',
      haEntity: 'switch.ebro_luneta_termica_trasera',
      ...flag(data.rear_windshield_heat, 'Activa', 'Apagada', 'on', ''),
      details: 'Resistencias térmicas de la luneta posterior'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Purificador de Aire (Ionizador)',
      key: 'airPurification',
      haEntity: 'switch.ebro_purificador_aire',
      ...flag(data.air_purification, 'Activo', 'Apagado', 'on', ''),
      details: 'Sistema de filtrado y purificación de aire'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Calefacción Asiento Conductor',
      key: 'dSeatHeatingState',
      haEntity: 'sensor.ebro_asiento_calefactado_conductor',
      ...flag(seats.driver, 'Activa', 'Apagada', 'on', ''),
      details: 'Resistencias térmicas asiento conductor'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Calefacción Asiento Pasajero',
      key: 'pSeatHeatingState',
      haEntity: 'sensor.ebro_asiento_calefactado_pasajero',
      ...flag(seats.passenger, 'Activa', 'Apagada', 'on', ''),
      details: 'Resistencias térmicas asiento pasajero'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Calefacción Asientos Traseros',
      key: 'lSeatHeatingState2 / rSeatHeatingState2',
      haEntity: 'sensor.ebro_asientos_traseros_calefactados',
      value: (seats.rear_left || seats.rear_right) ? t('Izq: {p0} · Der: {p1}', {p0: seats.rear_left, p1: seats.rear_right}) : 'Apagados',
      tone: (seats.rear_left || seats.rear_right) ? 'on' : '',
      details: 'Resistencias térmicas plazas traseras'
    },
    {
      category: 'climate',
      catName: 'Climatización & Confort',
      name: 'Ventilación Asientos Delanteros',
      key: 'dSeatVentilateState / pSeatVentilateState',
      haEntity: 'sensor.ebro_asientos_ventilados',
      value: (seatsVent.driver || seatsVent.passenger) ? t('Conductor: {p0} · Copiloto: {p1}', {p0: seatsVent.driver ? 'ON' : 'OFF', p1: seatsVent.passenger ? 'ON' : 'OFF'}) : 'Apagada',
      tone: (seatsVent.driver || seatsVent.passenger) ? 'on' : '',
      details: 'Ventiladores de aire en tapicería'
    },

    // 6. Combustion engine & diagnostics
    {
      category: 'engine',
      catName: 'Motor & Diagnóstico',
      name: 'Motor Térmico de Gasolina',
      key: 'engineState',
      haEntity: 'binary_sensor.ebro_motor_termico',
      ...flag(data.engine_state, 'En Marcha', 'Apagado', 'on', ''),
      details: 'Estado del motor de combustión interna (ICE)'
    },
    {
      category: 'engine',
      catName: 'Motor & Diagnóstico',
      name: 'Conectividad Nube Oficial (Awake / Asleep)',
      key: 'onlineStatus',
      haEntity: 'binary_sensor.ebro_en_linea',
      ...flag(data.online_status, 'Coche Conectado y Despierto', 'Reposo / Datos en Nube', 'on', ''),
      details: 'Estado de comunicación del módem 4G / T-Box'
    },
    {
      category: 'engine',
      catName: 'Motor & Diagnóstico',
      name: 'Frescura de la Telemetría (Freshness)',
      key: 'freshness',
      haEntity: 'sensor.ebro_frescura_datos',
      value: data.freshness_text || 'Desconocido',
      tone: data.online_status ? 'on' : '',
      details: 'Indica si los datos son en tiempo real o el coche está durmiendo'
    },
    {
      category: 'engine',
      catName: 'Motor & Diagnóstico',
      name: 'Velocidad de Circulación',
      key: 'vehicleSpeed',
      haEntity: 'sensor.ebro_velocidad',
      value: `${Math.round(data.speed_kmh || 0)} km/h`,
      tone: (data.speed_kmh || 0) > 0 ? 'on' : '',
      details: 'Velocidad instantánea notificada por telemetría'
    },
    {
      category: 'engine',
      catName: 'Motor & Diagnóstico',
      name: 'Coordenadas GPS',
      key: 'latitude / longitude',
      haEntity: 'device_tracker.ebro_posicion',
      value: (data.latitude && data.longitude) ? `${data.latitude.toFixed(5)}, ${data.longitude.toFixed(5)}` : '--',
      statusClass: 'parked',
      details: t('Altitud: {p0} m | Rumbo: {p1}°', {p0: data.altitude || '--', p1: data.heading || '--'})
    }
  ];

  // Filtering
  const filtered = items.filter(item => {
    if (filterCategory !== 'all' && item.category !== filterCategory) return false;
    if (filterQuery) {
      const matchName = item.name.toLowerCase().includes(filterQuery);
      const matchKey = item.key.toLowerCase().includes(filterQuery);
      const matchVal = String(item.value).toLowerCase().includes(filterQuery);
      const matchEntity = false;   // it used to search by the Home Assistant entity name: no longer shown
      const matchCat = item.catName.toLowerCase().includes(filterQuery);
      return matchName || matchKey || matchVal || matchEntity || matchCat;
    }
    return true;
  });

  if (countBadge) {
    countBadge.innerText = t('Mostrando {p0} de {p1} sensores y cables integrados', {p0: filtered.length, p1: items.length});
  }

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="u-text-align-center_padding-2p5rem-1rem_color-var-text-muted">
        
        No se han encontrado sensores que coincidan con la búsqueda.
      </div>
    `;
    return;
  }

  // Grouped by category for a clean layout
  const grouped = {};
  filtered.forEach(it => {
    if (!grouped[it.catName]) grouped[it.catName] = [];
    grouped[it.catName].push(it);
  });

  let html = '';
  for (const [catTitle, list] of Object.entries(grouped)) {
    html += `
      <div class="sensor-group">
        <div class="sensor-group-head">
          <span>${escapeHtml(t(catTitle))}</span>
          <span class="sensor-group-count">${escapeHtml(t('{p0} sensores', { p0: list.length }))}</span>
        </div>
        <div class="sensor-list">
    `;

    list.forEach(s => {
      const tone = sensorTone(s);
      html += `
        <div class="sensor-row">
          <div class="sensor-main">
            <div class="sensor-name">${escapeHtml(t(s.name))}</div>
            <div class="sensor-key">${escapeHtml(s.key)}</div>
            ${s.details ? `<div class="sensor-details">${escapeHtml(t(String(s.details)))}</div>` : ''}
          </div>
          <span class="sensor-value" data-tone="${tone}">${escapeHtml(t(String(s.value)))}</span>
        </div>
      `;
    });

    html += `
        </div>
      </div>
    `;
  }

  container.innerHTML = html;
}

/** The tone of a row: «Sin datos» is always muted; otherwise the reading's own tone or, for the rows that still give an old badge
 *  class, its meaning (warning = something open, danger = a warning from the car; any other = plain). */
function sensorTone(s) {
  const v = String(s.value);
  if (v === 'Sin datos' || v.startsWith('--')) return 'unknown';
  if (s.tone !== undefined) return s.tone;
  return { warning: 'warn', danger: 'alert' }[s.statusClass] || '';
}

/** «Actualizar» in the sensors modal: a fresh read, then the list. */
function refreshSensorsList() {
  return loadLatestTelemetry(currentVin).then(renderAllSensorsList);
}
