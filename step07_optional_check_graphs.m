fs = 1000;

t = (0:length(RESP)-1 / fs);

figure;

subplot(6,1,1)
plot(t, RESP)
title('RESP')
xlim([t(1) t(1)+30000])
grid on

subplot(6,1,2)
plot(t, RPIEZO)
title('Raw Piezo Signal')
xlim([t(1) t(1)+30000])
grid on

subplot(6,1,3)
plot(t, PIEZOF)
title('Filtered Piezo Signal')
xlim([t(1) t(1)+30000])
grid on

subplot(6,1,4)
plot(t, PIEZOD)
title('PIEZOD')
xlim([t(1) t(1)+30000])
grid on

subplot(6,1,5)
plot(t, STIMTRIG)
title('STIMTRIG')
xlim([t(1) t(1)+30000])
grid on

subplot(6,1,6)
plot(t, MRTRIG)
title('MRTRIG')
xlim([t(1) t(1)+30000])
grid on